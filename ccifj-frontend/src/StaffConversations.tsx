import { useEffect, useState } from 'react';
import { getStaffConversation, getStaffConversations } from './api';
import type { Locale, StaffConversation, StaffConversationItem } from './types';

const words = {
  en: {note: 'What visitors asked and what the agent answered. Read-only. Guest chats are kept only when the visitor confirmed a request; signed-in chats are always kept.', all: 'All conversations', withRequest: 'With a request only', loading: 'Loading…', empty: 'No conversations in this view.', failed: 'Could not load conversations.', retry: 'Try again', more: 'Load more', guest: 'Guest visitor', messages: 'messages', open: 'Read the conversation', visitor: 'Visitor', agent: 'CCIFJ agent', sources: 'Sources cited', gone: 'This conversation is no longer available.', request: 'Request'},
  fr: {note: 'Ce que les visiteurs ont demandé et ce que l’agent a répondu. Lecture seule. Les conversations d’invités ne sont conservées que si le visiteur a confirmé une demande ; celles des comptes connectés le sont toujours.', all: 'Toutes les conversations', withRequest: 'Avec une demande seulement', loading: 'Chargement…', empty: 'Aucune conversation dans cette vue.', failed: 'Chargement impossible.', retry: 'Réessayer', more: 'Afficher plus', guest: 'Visiteur invité', messages: 'messages', open: 'Lire la conversation', visitor: 'Visiteur', agent: 'Agent CCIFJ', sources: 'Sources citées', gone: 'Cette conversation n’est plus disponible.', request: 'Demande'},
  ja: {note: '訪問者の質問とエージェントの回答です（閲覧のみ）。ゲストの会話は、問い合わせを確定した場合のみ保存されます。ログイン済みの会話は常に保存されます。', all: 'すべての会話', withRequest: '問い合わせありのみ', loading: '読み込み中…', empty: '会話はありません。', failed: '読み込めませんでした。', retry: '再試行', more: 'さらに表示', guest: 'ゲスト', messages: '件のメッセージ', open: '会話を読む', visitor: '訪問者', agent: 'CCIFJエージェント', sources: '引用した情報源', gone: 'この会話は利用できません。', request: '問い合わせ'},
};

const when = (locale: Locale, value: string) =>
  new Intl.DateTimeFormat(locale, {dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Tokyo'}).format(new Date(value)) + ' JST';

/** One conversation as the visitor saw it; loaded when first shown. */
export function ConversationThread({conversationId, locale}: {conversationId: number; locale: Locale}) {
  const [data, setData] = useState<StaffConversation | null>(null);
  const [error, setError] = useState(false);
  const t = words[locale];
  useEffect(() => {
    let active = true;
    setData(null); setError(false);
    getStaffConversation(conversationId).then(result => { if (active) setData(result); }).catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, [conversationId]);
  if (error) return <p role="alert">{t.gone}</p>;
  if (!data) return <p role="status">{t.loading}</p>;
  return <ol className="conversation-thread">{data.messages.map((message, index) => <li key={index} className={`conversation-message ${message.role}`}>
    <p className="conversation-author">{message.role === 'user' ? (data.visitor.guest ? t.guest : data.visitor.name || t.visitor) : t.agent}
      <time dateTime={message.created_at}>{when(locale, message.created_at)}</time></p>
    <p className="conversation-text" dir="auto">{message.content}</p>
    {message.sources.length > 0 && <p className="conversation-sources">{t.sources}: {message.sources.map((source, n) =>
      <span key={n}>{n > 0 && ' · '}{/^https:\/\//.test(source.url)
        ? <a href={source.url} target="_blank" rel="noreferrer">{source.title || source.url}</a> : source.title || source.url}</span>)}</p>}
  </li>)}</ol>;
}

/** A collapsed "Read the conversation" row; the transcript is fetched the first time it is opened. */
export function ConversationDetails({conversationId, locale}: {conversationId: number; locale: Locale}) {
  const [seen, setSeen] = useState(false);
  return <details onToggle={event => { if (event.currentTarget.open) setSeen(true); }}>
    <summary>{words[locale].open}</summary>
    {seen && <ConversationThread conversationId={conversationId} locale={locale} />}
  </details>;
}

export function StaffConversations({locale}: {locale: Locale}) {
  const [withRequest, setWithRequest] = useState(false), [offset, setOffset] = useState(0), [revision, setRevision] = useState(0);
  const [items, setItems] = useState<StaffConversationItem[]>([]), [next, setNext] = useState<number | null>(null);
  const [busy, setBusy] = useState(true), [error, setError] = useState(false);
  const t = words[locale];
  useEffect(() => {
    let active = true; setBusy(true); setError(false);
    getStaffConversations(withRequest, offset).then(result => { if (active) { setItems(old => offset ? [...old, ...result.items] : result.items); setNext(result.next_offset); } })
      .catch(() => { if (active) { setError(true); setItems([]); setNext(null); } }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [withRequest, offset, revision]);
  return <>
    <p className="inbox-note">{t.note}</p>
    <label className="inbox-filter"><select value={withRequest ? 'request' : ''} disabled={busy} aria-label={t.all}
      onChange={event => { setItems([]); setOffset(0); setWithRequest(event.target.value === 'request'); }}>
      <option value="">{t.all}</option><option value="request">{t.withRequest}</option></select></label>
    {busy && <p role="status">{t.loading}</p>}
    {error && <p role="alert">{t.failed} <button type="button" onClick={() => setRevision(value => value + 1)}>{t.retry}</button></p>}
    {!busy && !error && !items.length && <p className="inbox-empty">{t.empty}</p>}
    <div className="inbox-items">{items.map(item => <article className="inbox-request" key={item.conversation_id}>
      <div className="inbox-request-meta">
        <strong>{item.visitor.guest ? t.guest : `${item.visitor.name} <${item.visitor.email}>`}</strong>
        <span>{item.message_count} {t.messages}</span>
        {item.requests.map(request => <span key={request.reference} title={request.subject}>{t.request} <code>{request.reference}</code></span>)}
        <time dateTime={item.last_at}>{when(locale, item.last_at)}</time>
      </div>
      <h2 dir="auto">{item.preview || '…'}</h2>
      <ConversationDetails conversationId={item.conversation_id} locale={locale} />
    </article>)}</div>
    {next !== null && !error && <button className="inbox-more" type="button" disabled={busy} onClick={() => setOffset(next)}>{t.more}</button>}
  </>;
}
