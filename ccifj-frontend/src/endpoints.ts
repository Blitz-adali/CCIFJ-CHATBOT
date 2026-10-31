/** Backend routes. The standalone site uses these routes for its assistant. */
export const API_ENDPOINTS = {
  sessions: '/api/v1/sessions',
  history: (sessionId: string) => `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`,
  claim: (sessionId: string) => `/api/v1/sessions/${encodeURIComponent(sessionId)}/claim`,
  reconcile: (sessionId: string) => `/api/v1/sessions/${encodeURIComponent(sessionId)}/reconcile`,
  status: '/api/v1/status', // GET: local API process readiness
  suggestions: '/api/v1/suggestions', // GET: predefined question catalog
  chat: '/api/v1/chat',     // POST: { session_id, message, locale }
  chatStream: '/api/v1/chat/stream',
  chatSocket: '/api/v1/chat/ws',
  approval: (sessionId: string) => `/api/v1/sessions/${encodeURIComponent(sessionId)}/approvals`,
  login: '/api/v1/auth/login',
  register: '/api/v1/auth/register',
} as const;
export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');
