import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, AUTH_EXPIRED_EVENT, reconcileSession, resolveApproval, sendChat, sendChatStream } from "./api";

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


describe("Sara backend contract", () => {
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

  it("maps the backend reply field to answer and keeps guided path data", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({
      session_id: "signed-session", reply: "## Upcoming CCIFJ events", mode: "guided_path",
      path_actions: [{ id: "events.details", event_id: "23", label: "View event details: Gala" }],
    })));
    const result = await sendChat({ session_id: null, message: "Upcoming", locale: "en", path_step_id: "events.upcoming" });
    expect(result.answer).toBe("## Upcoming CCIFJ events");
    expect(result.path_actions?.[0].event_id).toBe("23");
    expect(result.pending_approvals).toEqual([]);
  });

  it("posts approval decisions to the session approvals route without the session id in the body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ session_id: "s/1", reply: "Booked." }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await resolveApproval({ session_id: "s/1", interrupt_id: "i-1", decision: "approve", locale: "fr" });
    expect(result.answer).toBe("Booked.");
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/sessions/s%2F1/approvals", expect.objectContaining({
      method: "POST", body: JSON.stringify({ interrupt_id: "i-1", decision: "approve", locale: "fr" }),
    }));
  });

  it("reads structured error details and the uncertain-outcome flag", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ detail: { message: "Check the previous request outcome before continuing.", outcome_unknown: true } }, 409)));
    const error = await sendChat({ session_id: "s", message: "Hello", locale: "en" }).catch(cause => cause);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.message).toBe("Check the previous request outcome before continuing.");
    expect(error.outcomeUnknown).toBe(true);
    expect(error.status).toBe(409);
  });

  it("surfaces stream progress and stream error events", async () => {
    const stream = 'event: progress\ndata: {"messages": ["Checking our events database…"]}\n\n'
      + 'event: error\ndata: {"status": 502, "message": "Agent failed", "outcome_unknown": true}\n\n';
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(stream, { status: 200 })));
    const progress: string[][] = [];
    const error = await sendChatStream({ session_id: "s", message: "events", locale: "en" }, messages => progress.push(messages)).catch(cause => cause);
    expect(progress).toEqual([["Checking our events database…"]]);
    expect(error.message).toBe("Agent failed");
    expect(error.outcomeUnknown).toBe(true);
  });

  it("drops a rejected login and notifies the app", async () => {
    const storage = new Map<string, string>();
    const listener = vi.fn();
    vi.stubGlobal("localStorage", { getItem: (k: string) => storage.get(k) ?? null, setItem: (k: string, v: string) => storage.set(k, v), removeItem: (k: string) => storage.delete(k) });
    vi.stubGlobal("window", { dispatchEvent: (event: Event) => listener(event.type) });
    storage.set("ccifj-auth-session", JSON.stringify({ access_token: "opaque", token_type: "bearer", user: { user_id: 1, name: "A", email: "a@example.com" } }));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ detail: "Invalid or expired bearer token." }, 401)));
    await expect(sendChat({ session_id: null, message: "Hi", locale: "en" })).rejects.toBeInstanceOf(ApiError);
    expect(storage.has("ccifj-auth-session")).toBe(false);
    expect(listener).toHaveBeenCalledWith(AUTH_EXPIRED_EVENT);
  });

  it("acknowledges an uncertain outcome through the reconcile route", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ session_id: "s" }));
    vi.stubGlobal("fetch", fetchMock);
    await reconcileSession("s");
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/sessions/s/reconcile", expect.objectContaining({ body: JSON.stringify({ checked_outcome: true }) }));
  });
});
