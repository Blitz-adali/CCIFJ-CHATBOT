import type { ChatResponse } from './types';

export interface SavedTurn {
  id: number;
  role: 'user' | 'assistant';
  text: string;
  response?: ChatResponse;
  error?: boolean;
  cancelled?: boolean;
  retry?: string;
  /** Names of files attached to a user message (their text stays on the server). */
  files?: string[];
}

export interface SavedConversation {
  sessionId: string;
  title: string;
  updatedAt: number;
  turns: SavedTurn[];
}

const MAX_SAVED_CONVERSATIONS = 30;

function storageFor(userId: number | null): Storage | null {
  if (userId === null) return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function keyFor(userId: number | null): string {
  return `ccifj-chat-history:user:${userId}`;
}

export function readConversationHistory(userId: number | null): SavedConversation[] {
  const storage = storageFor(userId);
  if (!storage) return [];
  try {
    const value = JSON.parse(storage.getItem(keyFor(userId)) || '[]') as unknown;
    if (!Array.isArray(value)) return [];
    return value.filter((item): item is SavedConversation =>
      Boolean(item) && typeof item === 'object' &&
      typeof (item as SavedConversation).sessionId === 'string' &&
      typeof (item as SavedConversation).title === 'string' &&
      typeof (item as SavedConversation).updatedAt === 'number' &&
      Array.isArray((item as SavedConversation).turns),
    ).sort((a, b) => b.updatedAt - a.updatedAt);
  } catch {
    return [];
  }
}

export function saveConversation(userId: number | null, conversation: SavedConversation): SavedConversation[] {
  const storage = storageFor(userId);
  if (!storage) return readConversationHistory(userId);
  const existing = readConversationHistory(userId).filter(item => item.sessionId !== conversation.sessionId);
  const next = [conversation, ...existing].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX_SAVED_CONVERSATIONS);
  try { storage.setItem(keyFor(userId), JSON.stringify(next)); } catch { /* Keep current chat usable if browser storage is full or disabled. */ }
  return next;
}
