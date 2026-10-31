/** Conversation lifecycle and API coordination, independent of React rendering. */
import { ApiError, claimChatSession, createChatSession, getChatHistory, getSavedAuthSession, reconcileChatSession, sendLiveChat, resolveApproval, subscribeAuth } from './api';
import type { AuthChange } from './auth-session';
import type { ChatResponse, Locale, PendingApproval } from './types';

export type Turn = { id: number; role: 'user' | 'assistant'; text: string; response?: ChatResponse; error?: boolean; retry?: string; localOnly?: boolean };
type Saved = { session_id: string; uncertain: boolean; request_id?: string };
export type ChatState = { turns: Turn[]; sessionId: string | null; busy: boolean; progress: string[]; streamingText: string; approvals: PendingApproval[]; outcome: 'ready' | 'processing' | 'unknown' | 'login'; notice: string };
const memory = new Map<string, Saved>();
function key(owner: number | null) { return `ccifj-conversation:${owner ?? 'guest'}`; }
function read(owner: number | null): Saved | null {
  const name = key(owner);
  if (memory.has(name)) return memory.get(name)!;
  try {
    const item = JSON.parse(localStorage.getItem(name) || 'null');
    if (typeof item?.session_id === 'string') { memory.set(name, item); return item; }
  } catch { /* Session persistence is optional. */ }
  return null;
}
function write(owner: number | null, saved: Saved | null) {
  const name = key(owner);
  if (saved) memory.set(name, saved); else memory.delete(name);
  try { if (saved) localStorage.setItem(name, JSON.stringify(saved)); else localStorage.removeItem(name); } catch { /* Keep the current conversation in memory. */ }
}
const empty = (): ChatState => ({ turns: [], sessionId: null, busy: false, progress: [], streamingText: '', approvals: [], outcome: 'ready', notice: '' });

export class ChatSession {
  state: ChatState = empty();
  private listeners = new Set<() => void>();
  private owner = getSavedAuthSession()?.user.user_id ?? null;
  private generation = 0;
  private controller?: AbortController;
  private unsubscribe?: () => void;
  private poll?: ReturnType<typeof setTimeout>;
  private nextId = 0;
  private requestId = read(this.owner)?.request_id;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  snapshot = () => this.state;
  private update(patch: Partial<ChatState>) { this.state = { ...this.state, ...patch }; this.listeners.forEach(listener => listener()); }
  private invalidate() {
    this.generation++;
    this.controller?.abort();
    clearTimeout(this.poll);
  }
  private begin() {
    this.invalidate();
    this.controller = new AbortController();
    this.update({ busy: true, progress: [], streamingText: '' });
    return { generation: this.generation, signal: this.controller.signal };
  }
  start() {
    this.unsubscribe = subscribeAuth(change => { void this.authChanged(change); });
    const saved = read(this.owner);
    if (saved) void this.load(saved.session_id);
  }
  stop() { this.invalidate(); this.unsubscribe?.(); }
  private persist(uncertain = false) {
    if (this.state.sessionId) write(this.owner, { session_id: this.state.sessionId, uncertain, request_id: this.requestId });
  }
  private async authChanged(change: AuthChange) {
    this.invalidate(); // Synchronous: no old response can restore an old identity.
    if (change.reason === 'expired') {
      this.update({ busy: false, streamingText: '', progress: [], outcome: 'login', notice: 'Your sign-in expired. Log in again to continue this conversation.' });
      return;
    }
    const oldOwner = this.owner;
    const guest = oldOwner === null ? this.state.sessionId : null;
    this.owner = change.current?.user.user_id ?? null;
    const continuing = this.owner !== null && (oldOwner === this.owner || guest);
    const target = continuing ? this.state.sessionId : read(this.owner)?.session_id;
    // Never render a previous account's conversation after logout/account switching.
    if (!continuing) { this.requestId = read(this.owner)?.request_id; this.update(empty()); }

    if (target) await this.load(target, !!guest && this.owner !== null);
    else this.update({ busy: false, outcome: 'ready' });
  }
  private async load(id: string, claim = false, preserveFailure = false) {
    const operation = this.begin();
    this.update({ sessionId: id });
    try {
      if (claim) {
        await claimChatSession(id, operation.signal);
        if (operation.generation !== this.generation) return;
        this.persist(true);
        write(null, null);
      }
      const history = await getChatHistory(id, operation.signal);
      if (operation.generation !== this.generation) return;
      const turns: Turn[] = history.messages.filter(row => row.role !== 'tool').map(row => ({
        id: row.message_id, role: row.role as 'user' | 'assistant', text: row.content, response: row.response,
      }));
      this.nextId = Math.max(this.nextId, ...turns.map(turn => turn.id));
      if (preserveFailure) turns.push(...this.state.turns.filter(turn => turn.localOnly).map(turn => ({ ...turn, id: ++this.nextId })));
      const unconfirmed = read(this.owner)?.uncertain && this.requestId && history.last_request_id !== this.requestId;
      const outcome = history.turn_status === "ready" && unconfirmed ? "unknown" : history.turn_status;
      this.update({ sessionId: id, turns, approvals: history.pending_approvals, outcome, streamingText: '', busy: false, notice: outcome === 'ready' && !preserveFailure ? '' : this.state.notice });
      this.persist(outcome !== 'ready');
      if (history.turn_status === 'processing') this.poll = setTimeout(() => { void this.load(id); }, 2000);
    } catch (error) {
      if (operation.generation !== this.generation) return;
      if (error instanceof ApiError && error.status === 404) {
        write(this.owner, null);
        this.update({ ...empty(), notice: 'This conversation is unavailable or expired. Start a new conversation.' });
      } else this.update({ busy: false, outcome: error instanceof ApiError && error.status === 401 ? 'login' : 'unknown', notice: error instanceof Error ? error.message : 'Could not restore the conversation.' });
    }
  }
  recover = async () => { if (this.state.sessionId) await this.load(this.state.sessionId); };
  acknowledge = async () => {
    if (!this.state.sessionId || this.state.busy) return;
    const id = this.state.sessionId, operation = this.begin();
    try {
      await reconcileChatSession(id, operation.signal);
      if (operation.generation === this.generation) { this.update({ busy: false, notice: '' }); this.persist(false); await this.load(id); }
    } catch (error) { if (operation.generation === this.generation) this.update({ busy: false, notice: error instanceof Error ? error.message : 'Could not confirm the outcome.' }); }
  };
  newConversation = () => {
    this.invalidate();
    write(this.owner, null);
    this.owner = getSavedAuthSession()?.user.user_id ?? null;
    this.requestId = undefined;
    this.update(empty());
  };
  cancel = () => { this.controller?.abort(); };
  private async failure(error: unknown, text?: string, dispatched = true) {
    const api = error instanceof ApiError ? error : null;
    const cancelled = error instanceof DOMException && error.name === 'AbortError';
    if (api?.sessionId) this.update({ sessionId: api.sessionId });
    const notice = cancelled ? (dispatched ? 'Stopped waiting. The server may still be working; check the outcome before continuing.' : 'Stopped before sending the chat request. You can try again.') : error instanceof Error ? error.message : 'The request failed. Check its outcome before continuing.';
    const safe = !dispatched || (api?.safeToRetry && !api.outcomeUnknown);
    this.update({ busy: false, progress: [], streamingText: '', notice, outcome: api?.status === 401 ? 'login' : safe ? 'ready' : 'unknown',
      turns: [...this.state.turns, { id: ++this.nextId, role: 'assistant', text: notice, error: true, retry: safe ? text : undefined, localOnly: !!safe }],
    });
    this.persist(!safe);
    if (api?.status !== 401 && this.state.sessionId && (!safe || api?.status === 409)) await this.load(this.state.sessionId, false, !!safe);
  }
  ask = async (message: string, locale: Locale) => {
    message = message.trim();
    if (!message || this.state.busy || this.state.approvals.length || this.state.outcome !== 'ready') return;
    const operation = this.begin();
    const userTurnId = ++this.nextId;
    this.update({ notice: '', turns: [...this.state.turns, { id: userTurnId, role: 'user', text: message, localOnly: true }] });
    let dispatched = false;
    try {
      if (!this.state.sessionId) {
        const id = await createChatSession(operation.signal);
        if (operation.generation !== this.generation) return;
        this.update({ sessionId: id });
      }
      this.requestId = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
      this.persist(true); // Persist before dispatch: reload cannot silently replay a write.
      dispatched = true;
      const response = await sendLiveChat({ session_id: this.state.sessionId, message, locale, request_id: this.requestId },
        progress => { if (operation.generation === this.generation) this.update({ progress }); }, operation.signal,
        text => { if (operation.generation === this.generation) this.update({ streamingText: this.state.streamingText + text }); });
      if (operation.generation !== this.generation) return;
      this.update({ sessionId: response.session_id ?? this.state.sessionId, turns: [...this.state.turns.map(turn => turn.id === userTurnId ? { ...turn, localOnly: false } : turn), { id: ++this.nextId, role: 'assistant', text: response.answer, response }], approvals: response.pending_approvals ?? [], outcome: 'ready' });
      this.persist();
    } catch (error) { if (operation.generation === this.generation) await this.failure(error, message, dispatched); }
    finally { if (operation.generation === this.generation) this.update({ busy: false, progress: [], streamingText: '' }); }
  };
  retry = async (message: string, locale: Locale) => {
    if (this.state.busy || this.state.approvals.length || this.state.outcome !== "ready") return;
    this.update({ turns: this.state.turns.filter(turn => !turn.localOnly) });
    await this.ask(message, locale);
  };
  decide = async (interrupt_id: string, decision: 'approve' | 'reject', locale: Locale) => {
    if (!this.state.sessionId || this.state.busy || this.state.outcome !== 'ready') return;
    const operation = this.begin();
    this.requestId = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
    this.persist(true);
    try {
      const response = await resolveApproval({ session_id: this.state.sessionId, interrupt_id, decision, locale, request_id: this.requestId }, operation.signal);
      if (operation.generation !== this.generation) return;
      this.update({ turns: [...this.state.turns, { id: ++this.nextId, role: 'assistant', text: response.answer, response }], approvals: response.pending_approvals ?? [], outcome: 'ready', notice: '' });
      this.persist();
    } catch (error) { if (operation.generation === this.generation) await this.failure(error); }
    finally { if (operation.generation === this.generation) this.update({ busy: false, progress: [], streamingText: '' }); }
  };
}
