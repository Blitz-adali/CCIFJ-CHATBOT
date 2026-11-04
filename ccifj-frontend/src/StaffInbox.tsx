import { useEffect, useState } from 'react';
import { getStaffRequests } from './api';
import type { Locale, StaffRequestItem } from './types';

const words = {
  en: {title: 'Staff inbox', note: 'Approved requests queued for staff review. Emails are not sent automatically.', refresh: 'Refresh', all: 'All requests', new: 'New', in_review: 'In review', closed: 'Closed', loading: 'Loading requests…', empty: 'No requests in this view.', failed: 'Could not load requests.', more: 'Load more', from: 'From', to: 'To', team: 'CCIFJ team', retry: 'Try again'},
  fr: {title: 'Demandes reçues', note: 'Demandes approuvées en attente de traitement. Aucun e-mail n’est envoyé automatiquement.', refresh: 'Actualiser', all: 'Toutes les demandes', new: 'Nouvelles', in_review: 'En cours', closed: 'Closes', loading: 'Chargement…', empty: 'Aucune demande dans cette vue.', failed: 'Chargement impossible.', more: 'Afficher plus', from: 'De', to: 'À', team: 'Équipe CCIFJ', retry: 'Réessayer'},
  ja: {title: '問い合わせ受信箱', note: '承認済みの問い合わせをスタッフが確認します。メールは自動送信されません。', refresh: '更新', all: 'すべて', new: '新着', in_review: '確認中', closed: '完了', loading: '読み込み中…', empty: '問い合わせはありません。', failed: '読み込めませんでした。', more: 'さらに表示', from: '差出人', to: '宛先', team: 'CCIFJチーム', retry: '再試行'},
};

export function StaffInbox({locale}: {locale: Locale}) {
  const [filter,setFilter]=useState(''), [revision,setRevision]=useState(0), [offset,setOffset]=useState(0);
  const [items,setItems]=useState<StaffRequestItem[]>([]), [next,setNext]=useState<number|null>(null);
  const [busy,setBusy]=useState(true), [error,setError]=useState(false);
  const t=words[locale];
  useEffect(()=>{
    let active=true;setBusy(true);setError(false);
    getStaffRequests(filter,offset).then(result=>{if(active){setItems(old=>offset?[...old,...result.items]:result.items);setNext(result.next_offset);}})
      .catch(()=>{if(active){setError(true);setItems([]);setNext(null);}}).finally(()=>{if(active)setBusy(false);});
    return()=>{active=false;};
  },[filter,offset,revision]);
  return <section className="staff-inbox" aria-labelledby="inbox-title">
    <div className="inbox-heading"><div><p className="eyebrow">CCIFJ</p><h1 id="inbox-title">{t.title}</h1></div><button type="button" disabled={busy} onClick={()=>{setOffset(0);setRevision(value=>value+1);}}>{t.refresh}</button></div>
    <p className="inbox-note">{t.note}</p>
    <label className="inbox-filter"><span>{t.all}</span><select value={filter} disabled={busy} onChange={event=>{setItems([]);setOffset(0);setFilter(event.target.value);}}><option value="">{t.all}</option>{(['new','in_review','closed'] as const).map(status=><option key={status} value={status}>{t[status]}</option>)}</select></label>
    {busy&&<p role="status">{t.loading}</p>}
    {error&&<p role="alert">{t.failed} <button type="button" onClick={()=>setRevision(value=>value+1)}>{t.retry}</button></p>}
    {!busy&&!error&&!items.length&&<p className="inbox-empty">{t.empty}</p>}
    <div className="inbox-items">{items.map(item=><article className="inbox-request" key={item.reference}>
      <div className="inbox-request-meta"><code>{item.reference}</code><span>{t[item.status]}</span><time dateTime={item.created_at}>{new Intl.DateTimeFormat(locale,{dateStyle:'medium',timeStyle:'short',timeZone:'Asia/Tokyo'}).format(new Date(item.created_at))} JST</time></div>
      <h2>{item.subject}</h2><p>{t.from}: {item.sender_name} &lt;{item.sender_email}&gt;</p><p>{t.to}: {item.recipients.length?item.recipients.join(', '):t.team}</p>
      <details><summary>{locale==='fr'?'Lire le message':locale==='ja'?'メッセージを読む':'Read message'}</summary><p className="inbox-message" dir="auto">{item.message}</p></details>
    </article>)}</div>
    {next!==null&&!error&&<button className="inbox-more" type="button" disabled={busy} onClick={()=>setOffset(next)}>{t.more}</button>}
  </section>;
}
