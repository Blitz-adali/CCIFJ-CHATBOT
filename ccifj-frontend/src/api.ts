import { API_BASE_URL, API_ENDPOINTS } from "./endpoints";
import type { ApiStatusResponse, AuthSession, ChatRequest, ChatResponse, Locale } from "./types";
import type { PathAction } from "./types";
import englishQuestions from "../../src/concierge/data/predefined_qa.json";
import localizedQuestions from "../../src/concierge/data/predefined_qa_i18n.json";

const baseUrl = API_BASE_URL;
const timeoutMs = Number(import.meta.env.VITE_API_TIMEOUT_MS || 600000);
const AUTH_STORAGE_KEY = "ccifj-auth-session";
/** Fired on window when a saved login is rejected or has expired. */
export const AUTH_EXPIRED_EVENT = "ccifj:auth-expired";

/** Access tokens are JWTs; read their expiry so stale logins are dropped locally. */
function tokenExpiresAt(token: string): number | null {
  try {
    const payload = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const exp = (JSON.parse(atob(payload)) as { exp?: unknown }).exp;
    return typeof exp === "number" ? exp * 1000 : null;
  } catch {
    return null;
  }
}

export function getSavedAuthSession(): AuthSession | null {
  try {
    const parsed = JSON.parse(localStorage.getItem(AUTH_STORAGE_KEY) || "null") as AuthSession | null;
    if (!parsed || typeof parsed.access_token !== "string" || !parsed.user?.user_id) return null;
    const expiresAt = tokenExpiresAt(parsed.access_token);
    if (expiresAt !== null && expiresAt <= Date.now()) {
      clearAuthSession();
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function expireAuthSession(): void {
  if (!getSavedAuthSession()) return;
  clearAuthSession();
  try { window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT)); } catch { /* Non-browser test environment. */ }
}

export function clearAuthSession(): void {
  try { localStorage.removeItem(AUTH_STORAGE_KEY); } catch { /* Storage may be unavailable. */ }
}

function saveAuthSession(session: AuthSession): AuthSession {
  try { localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session)); } catch { /* Keep the in-memory login usable. */ }
  return session;
}

export class ApiError extends Error {
  readonly status?: number;
  readonly requestId?: string;
  /** The server could not confirm whether the request (e.g. a booking) ran. */
  readonly outcomeUnknown: boolean;

  constructor(message: string, status?: number, requestId?: string, outcomeUnknown = false) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.requestId = requestId;
    this.outcomeUnknown = outcomeUnknown;
  }
}

/** Build an ApiError from a backend error body (`detail` may be a string or an object). */
function errorFromBody(body: unknown, status: number, requestId?: string, authSent = false): ApiError {
  const record = typeof body === "object" && body !== null ? body as Record<string, unknown> : {};
  const detail = record.detail ?? record;
  const details = typeof detail === "object" && detail !== null ? detail as Record<string, unknown> : {};
  const text = typeof detail === "string" ? detail : details.message ?? record.message ?? record.error;
  if (status === 401 && authSent) expireAuthSession();
  return new ApiError(
    typeof text === "string" ? text : `Request failed (${status})`,
    status,
    requestId,
    details.outcome_unknown === true,
  );
}

/** The backend calls the answer `reply`; the UI uses `answer`. */
function normalizeResponse(raw: unknown): ChatResponse {
  const body = (typeof raw === "object" && raw !== null ? raw : {}) as ChatResponse & { reply?: unknown };
  const answer = typeof body.reply === "string" ? body.reply : body.answer;
  if (typeof answer !== "string") {
    throw new ApiError("The API response is missing its reply. Check the request outcome before continuing.", undefined, undefined, true);
  }
  return {
    ...body,
    answer,
    suggested_questions: Array.isArray(body.suggested_questions) ? body.suggested_questions : [],
    pending_approvals: Array.isArray(body.pending_approvals) ? body.pending_approvals : [],
    sources: Array.isArray(body.sources) ? body.sources : [],
    actions: Array.isArray(body.actions) ? body.actions : [],
    warnings: Array.isArray(body.warnings) ? body.warnings : [],
    path_actions: Array.isArray(body.path_actions) ? body.path_actions : [],
    next_actions: Array.isArray(body.next_actions) ? body.next_actions : [],
  };
}

export function getPathStepForQuestion(question: string, locale: Locale): string | undefined {
  const canonical = 'What events are coming up at CCIFJ?';
  const localized = locale === 'en' ? canonical : localizedQuestions[locale]?.[canonical]?.question;
  return localized === question.trim() ? 'events.upcoming' : undefined;
}

async function requestJson<T>(path: string, init: RequestInit = {}, timeout = timeoutMs, sendAuth = true): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const auth = sendAuth ? getSavedAuthSession() : null;
  try {
    const response = await fetch(`${baseUrl}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...(auth ? { Authorization: `Bearer ${auth.access_token}` } : {}),
        ...init.headers,
      },
    });

    const requestId = response.headers.get("x-request-id") ?? undefined;
    const raw = await response.text();
    let body: unknown = undefined;
    if (raw) {
      try {
        body = JSON.parse(raw) as unknown;
      } catch {
        body = raw;
      }
    }

    if (!response.ok) throw errorFromBody(body, response.status, requestId, Boolean(auth));
    return body as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError("The request timed out. Please try again.");
    }
    if (error instanceof TypeError) {
      throw new ApiError("Could not reach the API. Check the backend URL and that the backend is running.");
    }
    throw error instanceof Error ? error : new ApiError("An unexpected API error occurred.");
  } finally {
    clearTimeout(timer);
  }
}

export async function sendChat(request: ChatRequest): Promise<ChatResponse> {
  return normalizeResponse(await requestJson<unknown>(API_ENDPOINTS.chat, {
    method: "POST",
    body: JSON.stringify(request),
  }));
}

export async function sendChatStream(
  request: ChatRequest,
  onProgress: (messages: string[]) => void,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const abortFromCaller = () => controller.abort();
  signal?.addEventListener('abort', abortFromCaller, { once: true });
  try {
    const auth = getSavedAuthSession();
    const response = await fetch(`${baseUrl}${API_ENDPOINTS.chatStream}`, {
      method: 'POST',
      signal: controller.signal,
      headers: {
        Accept: 'text/event-stream',
        'Content-Type': 'application/json',
        ...(auth ? { Authorization: `Bearer ${auth.access_token}` } : {}),
      },
      body: JSON.stringify(request),
    });

    if (!response.ok) {
      const raw = await response.text();
      let body: unknown = undefined;
      try { body = JSON.parse(raw); } catch { /* Keep the HTTP status message. */ }
      throw errorFromBody(body, response.status, response.headers.get('x-request-id') ?? undefined, Boolean(auth));
    }
    if (!response.body) throw new ApiError('The API did not provide a streaming response.');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    const received: { result?: unknown; error?: ApiError } = {};

    const handleFrame = (frame: string) => {
      let eventType = 'message';
      const data: string[] = [];
      for (const line of frame.split(/\r?\n/)) {
        if (line.startsWith('event:')) eventType = line.slice(6).trim();
        else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
      }
      if (!data.length) return;
      const payload = JSON.parse(data.join('\n')) as Record<string, unknown>;
      if (eventType === 'progress') {
        if (typeof payload.message === 'string') onProgress([payload.message]);
        else if (Array.isArray(payload.messages)) {
          onProgress(payload.messages.filter((message): message is string => typeof message === 'string'));
        }
      }
      if (eventType === 'result') received.result = payload;
      if (eventType === 'error') {
        received.error = errorFromBody(payload, typeof payload.status === 'number' ? payload.status : 500, undefined, Boolean(auth));
      }
    };

    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() ?? '';
      for (const frame of frames) handleFrame(frame);
      if (done) break;
    }
    if (buffer.trim()) handleFrame(buffer);
    if (received.error) throw received.error;
    if (!received.result) {
      throw new ApiError('The API stream ended without a final answer. Check the request outcome before continuing.', undefined, undefined, true);
    }
    return normalizeResponse(received.result);
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('The request timed out. Please try again.');
    }
    if (error instanceof TypeError) {
      throw new ApiError('Could not reach the API. Check the backend URL and that the backend is running.');
    }
    throw error instanceof Error ? error : new ApiError('An unexpected API error occurred.');
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abortFromCaller);
  }
}

export async function loginAccount(email: string, password: string): Promise<AuthSession> {
  const session = await requestJson<AuthSession>(API_ENDPOINTS.login, {
    method: "POST", body: JSON.stringify({ email, password }),
  }, timeoutMs, false);
  return saveAuthSession(session);
}

export async function registerAccount(name: string, email: string, password: string): Promise<AuthSession> {
  const session = await requestJson<AuthSession>(API_ENDPOINTS.register, {
    method: "POST", body: JSON.stringify({ name, email, password }),
  }, timeoutMs, false);
  return saveAuthSession(session);
}

export async function resolveApproval({ session_id, ...decision }: {
  session_id: string;
  interrupt_id: string;
  decision: "approve" | "reject";
  locale: "en" | "fr" | "ja";
}): Promise<ChatResponse> {
  return normalizeResponse(await requestJson<unknown>(API_ENDPOINTS.approval(session_id), {
    method: "POST", body: JSON.stringify(decision),
  }));
}

/** Confirm the user checked an uncertain outcome; the server never replays it. */
export async function reconcileSession(sessionId: string): Promise<void> {
  await requestJson(API_ENDPOINTS.reconcile(sessionId), {
    method: "POST", body: JSON.stringify({ checked_outcome: true }),
  });
}

/** Attach the open guest conversation to the signed-in user so it is kept without a new message. */
export async function claimSession(sessionId: string): Promise<void> {
  // keepalive lets the claim finish if the user closes the page right after logging in.
  await requestJson(API_ENDPOINTS.claim(sessionId), { method: "POST", keepalive: true });
}

/** Ask the server to delete a guest conversation as the page closes. The server ignores owned sessions. */
export function discardGuestSession(sessionId: string): void {
  const url = `${baseUrl}${API_ENDPOINTS.discard(sessionId)}`;
  try {
    // A beacon without a body is a simple request: no CORS preflight, and it survives page unload.
    if (navigator.sendBeacon?.(url)) return;
  } catch { /* Fall back to fetch below. */ }
  try { void fetch(url, { method: "POST", keepalive: true }).catch(() => undefined); } catch { /* Best effort only. */ }
}

export async function getApiStatus(): Promise<ApiStatusResponse> {
  return requestJson<ApiStatusResponse>(API_ENDPOINTS.status, { method: "GET" }, 6000);
}

export async function getSuggestedQuestions(locale: Locale = "en"): Promise<string[]> {
  try {
    const response = await requestJson<{ suggested_questions?: unknown }>(`${API_ENDPOINTS.suggestions}?locale=${encodeURIComponent(locale)}`);
    if (Array.isArray(response?.suggested_questions)) {
      const questions = response.suggested_questions.filter((question): question is string => typeof question === "string");
      if (questions.length) return questions;
    }
  } catch { /* The bundled catalog still allows the welcome screen to render offline. */ }
  return getLocalSuggestedQuestions(locale);
}

export async function getStarterPaths(locale: Locale): Promise<PathAction[]> {
  const fallback: PathAction[] = [
    { id: 'events.upcoming', label: locale === 'fr' ? 'Événements CCIFJ à venir' : locale === 'ja' ? '今後のCCIFJイベント' : 'Browse upcoming CCIFJ events' },
    { id: 'spaces.start', label: locale === 'fr' ? 'Vérifier la disponibilité et réserver un espace' : locale === 'ja' ? '会場の空き状況を確認して予約' : 'Check space availability and book' },
  ];
  try {
    const response = await requestJson<{ starter_paths?: PathAction[] }>(`${API_ENDPOINTS.suggestions}?locale=${encodeURIComponent(locale)}`);
    return Array.isArray(response?.starter_paths) ? response.starter_paths : fallback;
  } catch {
    return fallback;
  }
}

export function getLocalSuggestedQuestions(locale: Locale): string[] {
  if (locale === "en") return englishQuestions.map(item => item.question);
  return Object.values(localizedQuestions[locale]).map(item => item.question);
}

export function isSafeExternalUrl(value: string | undefined): value is string {
  if (!value) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:";
  } catch {
    return false;
  }
}
