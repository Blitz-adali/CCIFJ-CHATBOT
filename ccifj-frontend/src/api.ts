import { API_BASE_URL as baseUrl, API_ENDPOINTS } from './endpoints';
import { getSavedAuthSession, saveAuthSession, invalidateAuth } from './auth-session';
import type { ApiStatusResponse, AuthSession, ChatHistory, ChatRequest, ChatResponse, PendingApproval } from './types';
export { getSavedAuthSession, clearAuthSession, subscribeAuth } from './auth-session';

const timeoutMs = Number(import.meta.env.VITE_API_TIMEOUT_MS || 600000);
type ErrorDetails = { session_id?: string; outcome_unknown?: boolean; safe_to_retry?: boolean; transport_unavailable?: boolean };
export class ApiError extends Error {
  readonly sessionId?: string;
  readonly outcomeUnknown: boolean;
  readonly safeToRetry: boolean;
  readonly transportUnavailable: boolean;
  constructor(message: string, readonly status?: number, readonly requestId?: string, details: ErrorDetails = {}) {
    super(message);
    this.name = 'ApiError';
    this.sessionId = details.session_id;
    this.outcomeUnknown = details.outcome_unknown ?? (status === undefined || status >= 500);
    this.safeToRetry = details.safe_to_retry ?? !this.outcomeUnknown;
    this.transportUnavailable = details.transport_unavailable ?? false;
  }
}
function record(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
function apiError(payload: unknown, status?: number, requestId?: string, token?: string): ApiError {
  const body = record(payload);
  const detail = body.detail ?? body;
  const fields = record(detail);
  const validation = Array.isArray(detail) ? detail.map(item => record(item).msg).filter(item => typeof item === 'string').join('; ') : '';
  const message = validation || (typeof detail === 'string' ? detail : typeof fields.message === 'string' ? fields.message : `Request failed (${status ?? 'connection lost'})`);
  if (status === 401) invalidateAuth(token);
  return new ApiError(message, status, requestId, fields as ErrorDetails);
}
export function normalizeApprovals(value: unknown): PendingApproval[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap(item => {
    const approval = record(item);
    if (typeof approval.interrupt_id !== 'string') return [];
    const actions = approval.actions ?? approval.action_requests;
    return [{ interrupt_id: approval.interrupt_id, actions: Array.isArray(actions) ? actions.flatMap(item => {
      const action = record(item);
      return typeof action.name === 'string' ? [{ name: action.name, args: record(action.args), description: typeof action.description === 'string' ? action.description : undefined }] : [];
    }) : [] }];
  });
}
export function normalizeChat(value: unknown): ChatResponse {
  const body = record(value);
  const answer = body.reply ?? body.answer;
  if (typeof answer !== 'string') throw new ApiError('The API response is missing its reply. Check the request outcome before continuing.');
  return {
    ...body, answer,
    suggested_questions: Array.isArray(body.suggested_questions) ? body.suggested_questions.filter((item): item is string => typeof item === 'string') : [],
    pending_approvals: normalizeApprovals(body.pending_approvals),
    sources: Array.isArray(body.sources) ? body.sources : [],
    actions: Array.isArray(body.actions) ? body.actions : [],
    warnings: Array.isArray(body.warnings) ? body.warnings : [],
  } as ChatResponse;
}
async function requestJson<T>(path: string, init: RequestInit = {}, timeout = timeoutMs): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  init.signal?.addEventListener('abort', abort, { once: true });
  if (init.signal?.aborted) controller.abort();
  const timer = setTimeout(abort, timeout);
  const token = getSavedAuthSession()?.access_token;
  try {
    const response = await fetch(`${baseUrl}${path}`, {
      ...init, signal: controller.signal,
      headers: { Accept: 'application/json', ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init.headers },
    });
    const raw = await response.text();
    let body: unknown;
    try { body = raw ? JSON.parse(raw) : null; } catch { body = { message: raw }; }
    if (!response.ok) throw apiError(body, response.status, response.headers.get('x-request-id') ?? undefined, token);
    return body as T;
  } catch (error) {
    if (init.signal?.aborted) throw new DOMException('Stopped waiting', 'AbortError');
    if (error instanceof ApiError) throw error;
    throw new ApiError(controller.signal.aborted ? 'The request timed out. Check its outcome before continuing.' : 'Could not reach the API. Check its connection and the request outcome.');
  } finally {
    clearTimeout(timer);
    init.signal?.removeEventListener('abort', abort);
  }
}
export async function sendChat(request: ChatRequest, signal?: AbortSignal): Promise<ChatResponse> {
  return normalizeChat(await requestJson(API_ENDPOINTS.chat, { method: 'POST', body: JSON.stringify(request), signal }));
}
export async function sendChatStream(request: ChatRequest, onProgress: (messages: string[]) => void, signal?: AbortSignal, onDelta?: (text: string) => void): Promise<ChatResponse> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  const timer = setTimeout(abort, timeoutMs);
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) controller.abort();
  const token = getSavedAuthSession()?.access_token;
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  try {
    const response = await fetch(`${baseUrl}${API_ENDPOINTS.chatStream}`, {
      method: 'POST', signal: controller.signal, body: JSON.stringify(request),
      headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    });
    if (!response.ok) {
      const raw = await response.text();
      let payload: unknown;
      try { payload = JSON.parse(raw); } catch { payload = { message: raw }; }
      const error = apiError(payload, response.status, undefined, token);
      if ([404, 405].includes(response.status)) throw new ApiError(error.message, response.status, undefined, { safe_to_retry: true, outcome_unknown: false, transport_unavailable: true });
      throw error;
    }
    if (!response.body) throw new ApiError('The API stream has no body. Check the request outcome.');
    reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let result: ChatResponse | undefined;
    let failure: ApiError | undefined;
    function frame(text: string) {
      let kind = 'message';
      const data: string[] = [];
      for (const line of text.split(/\r?\n/)) {
        if (line.startsWith('event:')) kind = line.slice(6).trim();
        if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
      }
      if (!data.length) return;
      const body = record(JSON.parse(data.join('\n')));
      if (kind === 'progress') onProgress(typeof body.message === 'string' ? [body.message] : Array.isArray(body.messages) ? body.messages.filter((v): v is string => typeof v === 'string') : []);
      if (kind === 'delta' && typeof body.text === 'string') onDelta?.(body.text);
      if (kind === 'result') result = normalizeChat(body);
      if (kind === 'error') failure = apiError(body, typeof body.status === 'number' ? body.status : undefined, undefined, token);
    }
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() ?? '';
      frames.forEach(frame);
      if (done) break;
    }
    if (buffer.trim()) frame(buffer);
    if (failure) throw failure;
    if (!result) throw new ApiError('The stream ended without a final answer. Check the request outcome.');
    return result;
  } catch (error) {
    if (signal?.aborted) throw new DOMException('Stopped waiting', 'AbortError');
    if (error instanceof ApiError) throw error;
    throw new ApiError('Chat stream interrupted. Check the request outcome before continuing.');
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
    await reader?.cancel().catch(() => {});
    reader?.releaseLock();
  }
}
export async function sendChatSocket(request: ChatRequest, onProgress: (messages: string[]) => void, signal?: AbortSignal, onDelta?: (text: string) => void): Promise<ChatResponse> {
  return new Promise((resolve, reject) => {
    const token = getSavedAuthSession()?.access_token;
    if (signal?.aborted) { reject(new DOMException('Stopped waiting', 'AbortError')); return; }
    if (typeof WebSocket === 'undefined') { reject(new ApiError('WebSockets are unavailable.', undefined, undefined, { outcome_unknown: false, safe_to_retry: true, transport_unavailable: true })); return; }
    const url = new URL(`${baseUrl}${API_ENDPOINTS.chatSocket}`, window.location.href);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(url);
    let settled = false, sent = false;
    let timer: ReturnType<typeof setTimeout>;
    const abort = () => finish(undefined, new DOMException('Stopped waiting', 'AbortError'));
    const connectionError = () => new ApiError(sent ? 'Live chat disconnected. Check the request outcome.' : 'Live chat could not connect.', undefined, undefined, { outcome_unknown: sent, safe_to_retry: !sent, transport_unavailable: !sent });
    const finish = (result?: ChatResponse, error?: Error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
      socket.close();
      if (error) reject(error); else resolve(result!);
    };
    timer = setTimeout(() => finish(undefined, connectionError()), 8000);
    signal?.addEventListener('abort', abort, { once: true });
    socket.onopen = () => socket.send(JSON.stringify({ access_token: token }));
    socket.onmessage = event => {
      try {
        const body = record(JSON.parse(event.data));
        if (body.type === 'ready' && !sent) {
          sent = true;
          clearTimeout(timer);
          timer = setTimeout(() => finish(undefined, connectionError()), timeoutMs);
          socket.send(JSON.stringify(request));
        } else if (body.type === 'progress' && typeof body.message === 'string') onProgress([body.message]);
        else if (body.type === 'delta' && typeof body.text === 'string') onDelta?.(body.text);
        else if (body.type === 'error') finish(undefined, apiError(body, typeof body.status === 'number' ? body.status : undefined, undefined, token));
        else if (body.type === 'result') finish(normalizeChat(body));
      } catch { finish(undefined, new ApiError('Invalid chat response. Check the request outcome.')); }
    };
    socket.onerror = () => finish(undefined, connectionError());
    socket.onclose = () => finish(undefined, connectionError());
  });
}
export async function sendLiveChat(request: ChatRequest, onProgress: (messages: string[]) => void, signal?: AbortSignal, onDelta?: (text: string) => void): Promise<ChatResponse> {
  const mode = import.meta.env.VITE_CHAT_TRANSPORT;
  if (mode === 'json') return sendChat(request, signal);
  if (mode !== 'sse') {
    try { return await sendChatSocket(request, onProgress, signal, onDelta); }
    catch (error) { if (!(error instanceof ApiError) || !error.transportUnavailable || signal?.aborted) throw error; }
  }
  try { return await sendChatStream(request, onProgress, signal, onDelta); }
  catch (error) {
    if (!(error instanceof ApiError) || !error.transportUnavailable || signal?.aborted) throw error;
    return sendChat(request, signal);
  }
}
export async function loginAccount(email: string, password: string): Promise<AuthSession> {
  return saveAuthSession(await requestJson(API_ENDPOINTS.login, { method: 'POST', body: JSON.stringify({ email, password }) }));
}
export async function registerAccount(name: string, email: string, password: string): Promise<AuthSession> {
  return saveAuthSession(await requestJson(API_ENDPOINTS.register, { method: 'POST', body: JSON.stringify({ name, email, password }) }));
}
export async function resolveApproval(request: { session_id: string; interrupt_id: string; decision: 'approve' | 'reject'; locale: 'en' | 'fr' | 'ja'; request_id?: string }, signal?: AbortSignal): Promise<ChatResponse> {
  const { session_id, interrupt_id, decision, locale, request_id } = request;
  return normalizeChat(await requestJson(API_ENDPOINTS.approval(session_id), { method: 'POST', body: JSON.stringify({ interrupt_id, decision, locale, request_id }), signal }));
}
export async function createChatSession(signal?: AbortSignal): Promise<string> {
  return (await requestJson<{ session_id: string }>(API_ENDPOINTS.sessions, { method: 'POST', signal })).session_id;
}
export async function claimChatSession(id: string, signal?: AbortSignal): Promise<void> {
  await requestJson(API_ENDPOINTS.claim(id), { method: 'POST', signal });
}
export async function reconcileChatSession(id: string, signal?: AbortSignal): Promise<void> {
  await requestJson(API_ENDPOINTS.reconcile(id), { method: 'POST', body: JSON.stringify({ checked_outcome: true }), signal });
}
export async function getChatHistory(id: string, signal?: AbortSignal): Promise<ChatHistory> {
  let after = 0;
  const messages: ChatHistory['messages'] = [];
  while (true) {
    const page = await requestJson<ChatHistory>(`${API_ENDPOINTS.history(id)}?after_id=${after}&limit=100`, { signal });
    messages.push(...page.messages.map(row => ({ ...row, response: row.response ? normalizeChat(row.response) : undefined })));
    if (page.next_after_id === null) return { ...page, messages, pending_approvals: normalizeApprovals(page.pending_approvals) };
    after = page.next_after_id;
  }
}
export async function getApiStatus(): Promise<ApiStatusResponse> { return requestJson(API_ENDPOINTS.status, {}, 6000); }
export async function getSuggestedQuestions(): Promise<string[]> {
  const response = await requestJson<{ suggested_questions: unknown }>(API_ENDPOINTS.suggestions);
  return Array.isArray(response.suggested_questions) ? response.suggested_questions.filter((v): v is string => typeof v === 'string') : [];
}
export function watchApiStatus(listener: (online: boolean) => void): () => void {
  let active = true, timer: ReturnType<typeof setTimeout>;
  async function check() {
    try { const status = await getApiStatus(); if (active) listener(status.status === 'ok'); }
    catch { if (active) listener(false); }
    finally { if (active) timer = setTimeout(check, 15000); }
  }
  void check();
  return () => { active = false; clearTimeout(timer); };
}
export function isSafeExternalUrl(value: string | undefined): value is string {
  try { return !!value && new URL(value).protocol === 'https:'; } catch { return false; }
}
