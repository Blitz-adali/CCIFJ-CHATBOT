import { API_BASE_URL, API_ENDPOINTS } from "./endpoints";
import type { ApiStatusResponse, AuthSession, ChatRequest, ChatResponse } from "./types";

const baseUrl = API_BASE_URL;
const timeoutMs = Number(import.meta.env.VITE_API_TIMEOUT_MS || 600000);
const AUTH_STORAGE_KEY = "ccifj-auth-session";

export function getSavedAuthSession(): AuthSession | null {
  try {
    const parsed = JSON.parse(localStorage.getItem(AUTH_STORAGE_KEY) || "null") as AuthSession | null;
    return parsed && typeof parsed.access_token === "string" && parsed.user?.user_id ? parsed : null;
  } catch {
    return null;
  }
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

  constructor(message: string, status?: number, requestId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.requestId = requestId;
  }
}

async function requestJson<T>(path: string, init: RequestInit = {}, timeout = timeoutMs): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(`${baseUrl}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...(getSavedAuthSession() ? { Authorization: `Bearer ${getSavedAuthSession()!.access_token}` } : {}),
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

    if (!response.ok) {
      const record = typeof body === "object" && body !== null ? body as Record<string, unknown> : {};
      const detail = record.detail ?? record.message ?? record.error;
      const message = typeof detail === "string" ? detail : `Request failed (${response.status})`;
      throw new ApiError(message, response.status, requestId);
    }
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
  const response = await requestJson<ChatResponse>(API_ENDPOINTS.chat, {
    method: "POST",
    body: JSON.stringify(request),
  });
  if (!response || typeof response.answer !== "string") {
    throw new ApiError("The API response is missing the required 'answer' string.");
  }
  return {
    ...response,
    suggested_questions: Array.isArray(response.suggested_questions) ? response.suggested_questions : [],
    pending_approvals: Array.isArray(response.pending_approvals) ? response.pending_approvals : [],
    sources: Array.isArray(response.sources) ? response.sources : [],
    actions: Array.isArray(response.actions) ? response.actions : [],
    warnings: Array.isArray(response.warnings) ? response.warnings : [],
  };
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
      let message = `Request failed (${response.status})`;
      try {
        const body = JSON.parse(raw) as { detail?: unknown };
        if (typeof body.detail === 'string') message = body.detail;
      } catch { /* Keep the HTTP status message. */ }
      throw new ApiError(message, response.status, response.headers.get('x-request-id') ?? undefined);
    }
    if (!response.body) throw new ApiError('The API did not provide a streaming response.');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    const received: { result?: ChatResponse; error?: string } = {};

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
      if (eventType === 'result') received.result = payload as unknown as ChatResponse;
      if (eventType === 'error' && typeof payload.message === 'string') received.error = payload.message;
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
    if (received.error) throw new ApiError(received.error);
    const result = received.result;
    if (!result || typeof result.answer !== 'string') {
      throw new ApiError('The API stream ended without a final answer. Please try again.');
    }
    return {
      ...result,
      suggested_questions: Array.isArray(result.suggested_questions) ? result.suggested_questions : [],
      pending_approvals: Array.isArray(result.pending_approvals) ? result.pending_approvals : [],
      sources: Array.isArray(result.sources) ? result.sources : [],
      actions: Array.isArray(result.actions) ? result.actions : [],
      warnings: Array.isArray(result.warnings) ? result.warnings : [],
    };
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
  });
  return saveAuthSession(session);
}

export async function registerAccount(name: string, email: string, password: string): Promise<AuthSession> {
  const session = await requestJson<AuthSession>(API_ENDPOINTS.register, {
    method: "POST", body: JSON.stringify({ name, email, password }),
  });
  return saveAuthSession(session);
}

export async function resolveApproval(request: {
  session_id: string;
  interrupt_id: string;
  decision: "approve" | "reject";
  locale: "en" | "fr" | "ja";
}): Promise<ChatResponse> {
  const response = await requestJson<ChatResponse>(API_ENDPOINTS.approval, {
    method: "POST", body: JSON.stringify(request),
  });
  if (!response || typeof response.answer !== "string") {
    throw new ApiError("The API response is missing the required 'answer' string.");
  }
  return {
    ...response,
    suggested_questions: Array.isArray(response.suggested_questions) ? response.suggested_questions : [],
    pending_approvals: Array.isArray(response.pending_approvals) ? response.pending_approvals : [],
    sources: Array.isArray(response.sources) ? response.sources : [],
    actions: Array.isArray(response.actions) ? response.actions : [],
    warnings: Array.isArray(response.warnings) ? response.warnings : [],
  };
}

export async function getApiStatus(): Promise<ApiStatusResponse> {
  return requestJson<ApiStatusResponse>(API_ENDPOINTS.status, { method: "GET" }, 6000);
}

export async function getSuggestedQuestions(): Promise<string[]> {
  const response = await requestJson<{ suggested_questions?: unknown }>(API_ENDPOINTS.suggestions);
  return Array.isArray(response?.suggested_questions)
    ? response.suggested_questions.filter((question): question is string => typeof question === "string")
    : [];
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
