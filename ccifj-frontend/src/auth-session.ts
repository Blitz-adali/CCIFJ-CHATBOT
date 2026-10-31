import type { AuthSession } from './types';

const KEY = 'ccifj-auth-session';
export type AuthChange = { previous: AuthSession | null; current: AuthSession | null; reason: 'login' | 'logout' | 'expired' };
let loaded = false;
let current: AuthSession | null = null;
let expiryTimer: ReturnType<typeof setTimeout> | undefined;
const listeners = new Set<(change: AuthChange) => void>();
let storageTarget: Window | undefined;

function storedSession(raw: string | null): AuthSession | null {
  try {
    const saved = JSON.parse(raw || 'null');
    return saved?.user?.user_id && typeof saved.access_token === 'string' && expiration(saved) > Date.now() ? saved : null;
  } catch { return null; }
}
function storageChanged(event: StorageEvent) {
  if (event.key !== KEY && event.key !== null) return;
  let raw = event.newValue;
  // Read the newest snapshot: an older queued event must not undo a later login.
  try {
    if (event.storageArea && event.storageArea !== localStorage) return;
    raw = localStorage.getItem(KEY);
  } catch { /* Event data still permits synchronization if storage became unavailable. */ }
  const next = storedSession(raw);
  const previous = current;
  if (previous?.access_token === next?.access_token && previous?.user.user_id === next?.user.user_id) return;
  current = next;
  loaded = true;
  scheduleExpiry();
  // Do not write back: that would create a cross-tab notification loop.
  for (const listener of listeners) listener({ previous, current, reason: next ? 'login' : 'logout' });
}
function ensureStorageSync() {
  if (typeof window === 'undefined' || typeof window.addEventListener !== 'function' || storageTarget === window) return;
  storageTarget?.removeEventListener('storage', storageChanged);
  storageTarget = window;
  storageTarget.addEventListener('storage', storageChanged);
}

function expiration(session: AuthSession): number {
  try {
    const payload = session.access_token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const exp = JSON.parse(atob(payload)).exp;
    if (typeof exp === 'number') return exp * 1000;
  } catch { /* Opaque tokens can use the server's expires_in timestamp. */ }
  return session.expires_at ?? 0;
}
function scheduleExpiry() {
  clearTimeout(expiryTimer);
  if (current) expiryTimer = setTimeout(() => clearAuthSession('expired'), Math.max(0, Math.min(2147483647, expiration(current) - Date.now())));
}
export function getSavedAuthSession(): AuthSession | null {
  ensureStorageSync();
  if (!loaded) {
    loaded = true;
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) || 'null');
      if (saved?.user?.user_id && typeof saved.access_token === 'string' && expiration(saved) > Date.now()) current = saved;
      else localStorage.removeItem(KEY);
    } catch { /* In-memory authentication remains available. */ }
    scheduleExpiry();
  }
  if (current && expiration(current) <= Date.now()) clearAuthSession('expired');
  return current;
}
export function saveAuthSession(session: AuthSession): AuthSession {
  const previous = getSavedAuthSession();
  current = { ...session, expires_at: Date.now() + (session.expires_in ?? 3600) * 1000 };
  loaded = true;
  try { localStorage.setItem(KEY, JSON.stringify(current)); } catch { /* Memory is authoritative this tab. */ }
  scheduleExpiry();
  for (const listener of listeners) listener({ previous, current, reason: 'login' });
  return current;
}
export function clearAuthSession(reason: 'logout' | 'expired' = 'logout'): void {
  const previous = current;
  current = null;
  loaded = true;
  clearTimeout(expiryTimer);
  try { localStorage.removeItem(KEY); } catch { /* Optional persistence. */ }
  if (previous) for (const listener of listeners) listener({ previous, current, reason });
}
export function invalidateAuth(token: string | undefined) {
  if (token && current?.access_token === token) clearAuthSession('expired');
}
export function subscribeAuth(listener: (change: AuthChange) => void): () => void {
  ensureStorageSync();
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
