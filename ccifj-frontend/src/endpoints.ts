/** Backend routes. The standalone site uses these routes for its assistant. */
export const API_ENDPOINTS = {
  status: '/api/v1/status', // GET: local API process readiness
  suggestions: '/api/v1/suggestions', // GET: predefined question catalog
  chat: '/api/v1/chat',     // POST: { session_id, message, locale }
  chatStream: '/api/v1/chat/stream',
  approval: (sessionId: string) => `/api/v1/sessions/${encodeURIComponent(sessionId)}/approvals`,
  claim: (sessionId: string) => `/api/v1/sessions/${encodeURIComponent(sessionId)}/claim`,
  discard: (sessionId: string) => `/api/v1/sessions/${encodeURIComponent(sessionId)}/discard`,
  reconcile: (sessionId: string) => `/api/v1/sessions/${encodeURIComponent(sessionId)}/reconcile`,
  login: '/api/v1/auth/login',
  register: '/api/v1/auth/register',
  currentUser: '/api/v1/auth/me',
} as const;
export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');
