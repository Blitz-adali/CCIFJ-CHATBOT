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


describe('backend contract', () => {
  it('maps the backend reply into the displayed answer', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      session_id: 'opaque-token', reply: 'Backend reply', pending_approvals: [], suggested_questions: [],
    }))));
    expect((await sendChat({ session_id: null, message: 'Hello', locale: 'en' })).answer).toBe('Backend reply');
  });

  it('posts approval decisions to the session route with the requested answer language', async () => {
    const { resolveApproval } = await import('./api');
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ reply: 'Rejected' })));
    vi.stubGlobal('fetch', fetchMock);
    const response = await resolveApproval({ session_id: 'signed/token', interrupt_id: 'id-1', decision: 'reject', locale: 'fr' });
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/sessions/signed%2Ftoken/approvals', expect.objectContaining({
      body: JSON.stringify({ interrupt_id: 'id-1', decision: 'reject', locale: 'fr' }),
    }));
    expect(response.answer).toBe('Rejected');
  });

  it('parses fragmented SSE progress and the backend result', async () => {
    const { sendChatStream } = await import('./api');
    const encoder = new TextEncoder();
    const stream = new ReadableStream({ start(controller) {
      for (const chunk of ['event: progress\r\ndata: {"message":"Working"}\r', '\n\r\nevent: result\ndata: {"reply":"Bonjour","session_id":"signed"}\n\n']) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    } });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream)));
    const progress = vi.fn();
    const result = await sendChatStream({ session_id: null, message: 'Bonjour', locale: 'fr' }, progress);
    expect(progress).toHaveBeenCalledWith(['Working']);
    expect(result.answer).toBe('Bonjour');
    expect(result.session_id).toBe('signed');
  });

  it('authenticates a WebSocket and sends the chat after ready', async () => {
    const { sendChatSocket } = await import('./api');
    vi.stubGlobal('window', { location: { href: 'https://example.com/' } });
    class MockSocket {
      static instance: MockSocket;
      onopen?: () => void;
      onmessage?: (event: { data: string }) => void;
      onclose?: () => void;
      onerror?: () => void;
      send = vi.fn();
      close = vi.fn();
      constructor(public url: URL) { MockSocket.instance = this; }
    }
    vi.stubGlobal('WebSocket', MockSocket);
    const request = { session_id: null, message: 'Hello', locale: 'en' as const };
    const pending = sendChatSocket(request, vi.fn());
    const socket = MockSocket.instance;
    expect(socket.url.href).toBe('wss://example.com/api/v1/chat/ws');
    socket.onopen!();
    expect(socket.send).toHaveBeenLastCalledWith('{}');
    socket.onmessage!({ data: '{"type":"ready"}' });
    expect(socket.send).toHaveBeenLastCalledWith(JSON.stringify(request));
    socket.onmessage!({ data: '{"type":"result","reply":"Hello","session_id":"signed"}' });
    expect((await pending).answer).toBe('Hello');
    expect(socket.close).toHaveBeenCalledOnce();
  });
});

describe('safe transport recovery',()=>{
  it('falls back to JSON only when the SSE route rejects the request before execution',async()=>{
    const {sendLiveChat}=await import('./api');
    vi.stubGlobal('WebSocket',undefined);
    vi.stubGlobal('window',{location:{href:'https://example.com/'}});
    const fetchMock=vi.fn().mockResolvedValueOnce(new Response('Not found',{status:404})).mockResolvedValueOnce(new Response(JSON.stringify({reply:'JSON answer'})));
    vi.stubGlobal('fetch',fetchMock);
    expect((await sendLiveChat({session_id:null,message:'Hello',locale:'en'},vi.fn())).answer).toBe('JSON answer');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it('preserves SSE error status and uncertainty instead of replaying a failed write',async()=>{
    const {sendLiveChat}=await import('./api');vi.stubGlobal('WebSocket',undefined);
    const fetchMock=vi.fn().mockResolvedValue(new Response('event: error\ndata: {"message":"Check booking","status":502,"session_id":"signed","outcome_unknown":true}\n\n'));
    vi.stubGlobal('fetch',fetchMock);
    await expect(sendLiveChat({session_id:'signed',message:'Book',locale:'en'},vi.fn())).rejects.toMatchObject({status:502,sessionId:'signed',outcomeUnknown:true,safeToRetry:false});
    expect(fetchMock).toHaveBeenCalledOnce();
  });
  it('normalizes legacy and missing approval action arrays',async()=>{
    const {normalizeChat}=await import('./api');
    expect(normalizeChat({reply:'Review',pending_approvals:[{interrupt_id:'a',action_requests:[{name:'newsletter',args:{email:'a@example.com'}}]},{interrupt_id:'b'}]}).pending_approvals).toEqual([
      {interrupt_id:'a',actions:[{name:'newsletter',args:{email:'a@example.com'},description:undefined}]},{interrupt_id:'b',actions:[]},
    ]);
  });
  it('retains partial answer chunks separately from the final structured result',async()=>{
    const {sendChatStream}=await import('./api');
    vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response('event: delta\ndata: {"text":"Bon"}\n\nevent: delta\ndata: {"text":"jour"}\n\nevent: result\ndata: {"reply":"Bonjour"}\n\n')));
    const delta=vi.fn();const result=await sendChatStream({session_id:null,message:'Hello',locale:'fr'},vi.fn(),undefined,delta);
    expect(delta.mock.calls).toEqual([['Bon'],['jour']]);expect(result.answer).toBe('Bonjour');
  });
  it('refreshes an offline status and recovers when the backend returns',async()=>{
    vi.useFakeTimers();
    const {watchApiStatus}=await import('./api');const listener=vi.fn();
    vi.stubGlobal('fetch',vi.fn().mockRejectedValueOnce(new TypeError('offline')).mockResolvedValue(new Response(JSON.stringify({status:'ok'}))));
    const stop=watchApiStatus(listener);await vi.advanceTimersByTimeAsync(1);expect(listener).toHaveBeenLastCalledWith(false);
    await vi.advanceTimersByTimeAsync(15000);expect(listener).toHaveBeenLastCalledWith(true);stop();
  });
});
