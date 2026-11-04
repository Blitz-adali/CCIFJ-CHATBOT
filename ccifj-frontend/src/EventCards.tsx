import { CalendarDays, MapPin, ArrowUpRight } from 'lucide-react';
import type { EventCardData, Locale } from './types';

const words = {
  en: { title: 'Events', details: 'Details', book: 'Prepare booking', open: 'Registration open', closed: 'Registration closed', unconfirmed: 'Registration unconfirmed', unknown: 'Not specified', price: 'Standard', member: 'Members', nonMember: 'Non-members', deadline: 'Register by', time: 'Japan time · JST', noPayment: 'No payment is taken. You review and approve before saving.' },
  fr: { title: 'Événements', details: 'Détails', book: 'Préparer la réservation', open: 'Inscriptions ouvertes', closed: 'Inscriptions closes', unconfirmed: 'Inscriptions à confirmer', unknown: 'Non précisé', price: 'Tarif standard', member: 'Membres', nonMember: 'Non-membres', deadline: 'Inscription avant le', time: 'Heure du Japon · JST', noPayment: 'Aucun paiement. Vous vérifiez et confirmez avant l’enregistrement.' },
  ja: { title: 'イベント', details: '詳細', book: '予約を準備', open: '受付中', closed: '受付終了', unconfirmed: '受付状況は未確認', unknown: '記載なし', price: '通常料金', member: '会員', nonMember: '非会員', deadline: '申込期限', time: '日本時間 · JST', noPayment: '支払いは発生しません。保存前に内容を確認し、承認できます。' },
};

export function formatJapanDate(value: string | null, locale: Locale): string {
  if (!value) return words[locale].unknown;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return words[locale].unknown;
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Tokyo' }).format(date);
}

export function EventCards({events = [], locale, disabled, onDetails, onBook}: {
  events?: EventCardData[]; locale: Locale; disabled: boolean;
  onDetails: (event: EventCardData) => void; onBook: (event: EventCardData) => void;
}) {
  if (!events.length) return null;
  const t = words[locale];
  function price(value: string | null, currency: string | null) {
    if (value === null) return null;
    const amount = Number(value);
    if (!Number.isFinite(amount)) return null;
    try { return currency ? new Intl.NumberFormat(locale, {style: 'currency', currency}).format(amount) : `${amount.toLocaleString(locale)} (${t.unknown})`; }
    catch { return `${amount.toLocaleString(locale)} (${t.unknown})`; }
  }
  return <section className="event-cards" aria-label={t.title}>
    {events.map(event=><article className="event-card" key={event.event_id}>
      <div className="event-card-top"><span className="event-category">{event.category}</span><span className={`registration-status ${event.registration_open===null?'unknown':event.registration_open?'open':'closed'}`}>{event.registration_open===null?t.unconfirmed:event.registration_open?t.open:t.closed}</span></div>
      <h3>{event.title}</h3>
      <p className="event-meta"><CalendarDays size={15} aria-hidden="true"/><span>{formatJapanDate(event.start_at,locale)}<small>{t.time}</small></span></p>
      <p className="event-meta"><MapPin size={15} aria-hidden="true"/>{event.location||t.unknown}</p>
      <dl className="event-prices">{([
        [t.price,price(event.price,event.currency)], [t.member,price(event.member_price,event.currency)], [t.nonMember,price(event.non_member_price,event.currency)],
      ] as const).filter(([,value])=>value!==null).map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
      {event.price===null&&event.member_price===null&&event.non_member_price===null&&<p className="event-deadline">{t.price}: {t.unknown}</p>}
      {event.reg_deadline&&<p className="event-deadline">{t.deadline}: {formatJapanDate(event.reg_deadline,locale)}</p>}
      <div className="event-card-actions"><button type="button" disabled={disabled} onClick={()=>onDetails(event)}>{t.details}<ArrowUpRight size={14} aria-hidden="true"/></button><button type="button" disabled={disabled||!event.registration_open} onClick={()=>onBook(event)}>{t.book}</button></div>
    </article>)}
    <p className="event-disclaimer">{t.noPayment}</p>
  </section>;
}
