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
  // FastAPI validation errors (422) carry a list: [{loc, msg, type}, ...]. Show the first message.
  const validation = Array.isArray(detail) ? (detail[0] as Record<string, unknown> | undefined)?.msg : undefined;
  const text = typeof detail === "string" ? detail
    : typeof validation === "string" ? `The request was rejected: ${validation}`
    : details.message ?? record.message ?? record.error;
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
    events: Array.isArray(body.events) ? body.events : [],
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

export interface ChatAccountLink {
  platform: 'whatsapp' | 'slack';
  /** The last characters of the phone number or Slack ID, e.g. "•••• 5678". */
  account_hint: string;
}

/** Which WhatsApp/Slack account a linking link belongs to. Does not use up the link. */
export async function previewAccountLink(token: string): Promise<ChatAccountLink> {
  return requestJson<ChatAccountLink>(API_ENDPOINTS.linkPreview, { method: "POST", body: JSON.stringify({ token }) });
}

/** Link that chat account to the signed-in user. The link works once. */
export async function completeAccountLink(token: string, locale: Locale): Promise<ChatAccountLink> {
  return requestJson<ChatAccountLink>(API_ENDPOINTS.linkComplete, { method: "POST", body: JSON.stringify({ token, locale }) });
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

export async function getCurrentUser(): Promise<import('./types').AuthUser> {
  return requestJson(API_ENDPOINTS.currentUser, { method: 'GET' }, 6000);
}

export async function getStaffRequests(status = '', offset = 0): Promise<{items: import('./types').StaffRequestItem[]; next_offset: number | null}> {
  const params = new URLSearchParams({ offset: String(offset) });
  if (status) params.set('status', status);
  return requestJson(`/api/v1/staff/requests?${params}`, { method: 'GET' }, 10000);
}

export async function updateStaffRequestStatus(reference: string, status: import('./types').StaffRequestItem['status']): Promise<void> {
  await requestJson(`/api/v1/staff/requests/${encodeURIComponent(reference)}`, { method: 'PATCH', body: JSON.stringify({ status }) }, 10000);
}

export async function getStaffConversations(withRequest = false, offset = 0): Promise<{items: import('./types').StaffConversationItem[]; next_offset: number | null}> {
  const params = new URLSearchParams({ offset: String(offset) });
  if (withRequest) params.set('with_request', 'true');
  return requestJson(`/api/v1/staff/conversations?${params}`, { method: 'GET' }, 10000);
}

export async function getStaffConversation(conversationId: number): Promise<import('./types').StaffConversation> {
  return requestJson(`/api/v1/staff/conversations/${conversationId}`, { method: 'GET' }, 10000);
}

// ---------- Admin dashboard (staff only) ----------

export async function getAdminOverview(): Promise<import('./types').AdminOverview> {
  return requestJson('/api/v1/admin/overview', { method: 'GET' }, 10000);
}

export async function getAdminUsers(q = '', offset = 0): Promise<{items: import('./types').AdminUser[]; next_offset: number | null}> {
  const params = new URLSearchParams({ offset: String(offset) });
  if (q.trim()) params.set('q', q.trim());
  return requestJson(`/api/v1/admin/users?${params}`, { method: 'GET' }, 10000);
}

export async function setUserRole(userId: number, role: 'user' | 'staff'): Promise<import('./types').AdminUser> {
  return requestJson(`/api/v1/admin/users/${userId}`, { method: 'PATCH', body: JSON.stringify({ role }) }, 10000);
}

export async function getStaffTopics(): Promise<import('./types').StaffTopic[]> {
  return requestJson('/api/v1/admin/topics', { method: 'GET' }, 10000);
}

export async function getStaffContacts(): Promise<import('./types').StaffContact[]> {
  return requestJson('/api/v1/admin/staff-contacts', { method: 'GET' }, 10000);
}

export type StaffContactInput = Partial<Omit<import('./types').StaffContact, 'contact_id'>>;

export async function saveStaffContact(contactId: number | null, body: StaffContactInput): Promise<import('./types').StaffContact> {
  return contactId === null
    ? requestJson('/api/v1/admin/staff-contacts', { method: 'POST', body: JSON.stringify(body) }, 10000)
    : requestJson(`/api/v1/admin/staff-contacts/${contactId}`, { method: 'PATCH', body: JSON.stringify(body) }, 10000);
}

export type EventInput = Partial<Omit<import('./types').AdminEvent, 'event_id'>>;

export async function getAdminEvents(q = '', scope: 'upcoming' | 'past' | 'all' = 'upcoming', offset = 0): Promise<{items: import('./types').AdminEvent[]; next_offset: number | null; categories: string[]}> {
  const params = new URLSearchParams({ scope, offset: String(offset) });
  if (q.trim()) params.set('q', q.trim());
  return requestJson(`/api/v1/admin/events?${params}`, { method: 'GET' }, 10000);
}

export async function saveAdminEvent(eventId: number | null, body: EventInput): Promise<import('./types').AdminEvent> {
  return eventId === null
    ? requestJson('/api/v1/admin/events', { method: 'POST', body: JSON.stringify(body) }, 10000)
    : requestJson(`/api/v1/admin/events/${eventId}`, { method: 'PUT', body: JSON.stringify(body) }, 10000);
}

export async function getAdminMembers(q = '', sector = '', offset = 0): Promise<{items: import('./types').AdminMember[]; next_offset: number | null; sectors: string[]}> {
  const params = new URLSearchParams({ offset: String(offset) });
  if (q.trim()) params.set('q', q.trim());
  if (sector) params.set('sector', sector);
  return requestJson(`/api/v1/admin/members?${params}`, { method: 'GET' }, 10000);
}

export async function getImportTemplate(kind: 'events' | 'members'): Promise<string> {
  return requestJson(`/api/v1/admin/import/${kind}/template`, { method: 'GET' }, 10000);
}

export async function importFile(kind: 'events' | 'members', file: import('./types').AttachmentUpload, commit: boolean): Promise<import('./types').ImportResult> {
  return requestJson(`/api/v1/admin/import/${kind}?commit=${commit}`, { method: 'POST', body: JSON.stringify(file) }, 120000);
}

export async function getKnowledgeDocs(): Promise<import('./types').KnowledgeDoc[]> {
  return requestJson('/api/v1/admin/knowledge', { method: 'GET' }, 10000);
}

export async function uploadKnowledgeDoc(file: import('./types').AttachmentUpload, title: string, sourceUrl: string): Promise<import('./types').KnowledgeDoc> {
  // Reading, OCR and embedding a long brochure can take a few minutes on CPU.
  return requestJson('/api/v1/admin/knowledge', { method: 'POST', body: JSON.stringify({ ...file, title, source_url: sourceUrl || null }) }, 600000);
}

export async function linkKnowledgeDoc(url: string, title: string, downloadUrl: string): Promise<import('./types').KnowledgeDoc> {
  return requestJson('/api/v1/admin/knowledge/link', { method: 'POST', body: JSON.stringify({ url, title, download_url: downloadUrl || null }) }, 600000);
}

export async function syncKnowledgeDoc(docId: number): Promise<import('./types').KnowledgeDoc & { changed: boolean }> {
  return requestJson(`/api/v1/admin/knowledge/${docId}/sync`, { method: 'POST' }, 600000);
}

export async function deleteKnowledgeDoc(docId: number): Promise<void> {
  await requestJson(`/api/v1/admin/knowledge/${docId}`, { method: 'DELETE' }, 60000);
}

export async function matchMembers(need: string, sector = '', region = ''): Promise<{items: import('./types').MemberMatch[]; relevant_score: number}> {
  const params = new URLSearchParams({ need, limit: '10' });
  if (sector) params.set('sector', sector);
  if (region.trim()) params.set('region', region.trim());
  // The first search after a change re-embeds the directory, which takes a few seconds.
  return requestJson(`/api/v1/admin/matching/members?${params}`, { method: 'GET' }, 120000);
}

export async function matchMembersForEvent(eventId: number): Promise<{need: string; items: import('./types').MemberMatch[]; relevant_score: number}> {
  return requestJson(`/api/v1/admin/matching/events/${eventId}/members?limit=10`, { method: 'GET' }, 120000);
}

export async function draftProposal(body: {need: string; requester: string; member_ids: number[]; language: string}): Promise<{draft: string; companies: string[]}> {
  return requestJson('/api/v1/admin/matching/proposal', { method: 'POST', body: JSON.stringify(body) }, 120000);
}

export interface InvitationDraft {
  member_id: number;
  company: string;
  language: string;
  to_name: string | null;
  to_email: string | null;
  subject: string;
  body: string;
}

export async function draftInvitations(body: {event_id: number; member_ids: number[]; language: string; note: string}): Promise<{event: string; items: InvitationDraft[]; warnings: string[]}> {
  return requestJson('/api/v1/admin/matching/invitations', { method: 'POST', body: JSON.stringify(body) }, 180000);
}

export async function getIntroRequests(status = ''): Promise<{items: import('./types').IntroRequestItem[]; next_offset: number | null}> {
  return requestJson(`/api/v1/admin/intro-requests${status ? `?status=${status}` : ''}`, { method: 'GET' }, 10000);
}

export async function updateIntroRequest(introId: number, body: {status?: import('./types').IntroStatus; staff_note?: string}): Promise<void> {
  await requestJson(`/api/v1/admin/intro-requests/${introId}`, { method: 'PATCH', body: JSON.stringify(body) }, 10000);
}

// ---------- Onboarding & automation ----------

export async function getAutomationRules(): Promise<import('./types').AutomationRule[]> {
  return requestJson('/api/v1/admin/automation/rules', { method: 'GET' }, 10000);
}

export async function updateAutomationRule(key: string, body: { enabled?: boolean; requires_approval?: boolean }): Promise<void> {
  await requestJson(`/api/v1/admin/automation/rules/${key}`, { method: 'PATCH', body: JSON.stringify(body) }, 10000);
}

export async function runAutomation(asOf: string): Promise<import('./types').RunResult> {
  // Event and member matching may embed new rows on the first run.
  return requestJson('/api/v1/admin/automation/run', { method: 'POST', body: JSON.stringify({ as_of: asOf || null }) }, 300000);
}

export async function getOutreach(status = ''): Promise<import('./types').OutreachItem[]> {
  return requestJson(`/api/v1/admin/outreach${status ? `?status=${status}` : ''}`, { method: 'GET' }, 10000);
}

export async function actOnOutreach(id: number, action: 'send' | 'skip' | 'save', edits: { subject?: string; body?: string } = {}): Promise<void> {
  await requestJson(`/api/v1/admin/outreach/${id}`, { method: 'PATCH', body: JSON.stringify({ action, ...edits }) }, 10000);
}

export async function rewriteOutreach(id: number, language: string): Promise<{ subject: string; body: string }> {
  return requestJson(`/api/v1/admin/outreach/${id}/rewrite`, { method: 'POST', body: JSON.stringify({ language }) }, 120000);
}

export async function getEngagement(): Promise<import('./types').EngagementRow[]> {
  return requestJson('/api/v1/admin/engagement', { method: 'GET' }, 120000);
}

export async function approveMembership(memberId: number, email: string): Promise<{ member: string; welcome: unknown[] }> {
  return requestJson(`/api/v1/admin/members/${memberId}/accounts`, { method: 'POST', body: JSON.stringify({ email }) }, 120000);
}

export async function getMyProfile(): Promise<import('./types').MemberProfile> {
  return requestJson('/api/v1/me/profile', { method: 'GET' }, 10000);
}

export async function saveMyProfile(body: { goals: string[]; interests: string; preferred_language: string; preferred_channel: string }): Promise<void> {
  await requestJson('/api/v1/me/profile', { method: 'PUT', body: JSON.stringify(body) }, 10000);
}

export async function getMyNotifications(): Promise<import('./types').MemberNotification[]> {
  return requestJson('/api/v1/me/notifications', { method: 'GET' }, 10000);
}

export async function markNotificationRead(id: number): Promise<void> {
  await requestJson(`/api/v1/me/notifications/${id}/read`, { method: 'POST' }, 10000);
}

export async function getAnalytics(days: number): Promise<import('./types').AnalyticsSummary> {
  return requestJson(`/api/v1/admin/analytics?days=${days}`, { method: 'GET' }, 60000);
}

export async function getLangsmithAnalytics(days: number): Promise<import('./types').LangsmithSummary> {
  // The first call pages through LangSmith runs; later calls are cached for 5 minutes.
  return requestJson(`/api/v1/admin/analytics/langsmith?days=${days}`, { method: 'GET' }, 180000);
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
