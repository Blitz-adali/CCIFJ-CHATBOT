import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, sendChat } from "./api";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("sendChat", () => {
  it("allows CPU generation to take longer than the old 30-second limit", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn().mockImplementation((_url, init) => new Promise((resolve, reject) => {
      init.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
      setTimeout(() => resolve(new Response(JSON.stringify({answer:'CPU answer', sources:[]}))), 45000);
    })));
    const pending=sendChat({session_id:null,message:'Set up a company in Japan',locale:'en'});
    await vi.advanceTimersByTimeAsync(45000);
    expect((await pending).answer).toBe('CPU answer');
  });
  it("posts the agreed multilingual chat contract and returns the response", async () => {
    const payload = {
      session_id: "session-123",
      message_id: "message-123",
      mode: "live",
      answer: "Use the official membership page.",
      intent: { code: "membership.join", confidence: 0.9 },
      sources: [{ id: "membership", title: "Membership", url: "https://www.ccifj.or.jp/en/membership.html" }],
      actions: [],
      warnings: [],
    };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await sendChat({ session_id: null, message: "How do I join?", locale: "en" });

    expect(fetchMock).toHaveBeenCalledWith("/api/v1/chat", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ session_id: null, message: "How do I join?", locale: "en" }),
    }));
    expect(result.answer).toBe("Use the official membership page.");
    expect(result.sources).toHaveLength(1);
  });

  it("rejects a response that does not contain an answer", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ sources: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })));
    await expect(sendChat({ session_id: null, message: "Hello", locale: "ja" })).rejects.toBeInstanceOf(ApiError);
  });
});

