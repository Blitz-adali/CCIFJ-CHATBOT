import type {ReactNode} from 'react';
import {ArrowRight, CalendarDays, Check, ShieldCheck, Ticket, X} from 'lucide-react';
import {DraftCard} from './DraftCard';
import type {ApprovalAction, Locale, PendingApproval} from './types';

type Args = Record<string, unknown>;

/** Approvals a guest may answer; every other action needs an account. */
const GUEST_ACTIONS = new Set(['submit_staff_request', 'save_introduction_request', 'save_member_followup']);
const CANCEL_ACTIONS = new Set(['cancel_event_booking', 'cancel_space_reservation']);

const words = {
  en: {
    review: 'Review before you confirm', login: 'Log in to approve', detail: 'Action details', reference: 'Reference', event: 'Event', eventId: 'Event ID',
    eventDate: 'Date and time', location: 'Venue', price: 'Price', free: 'Free', members: 'Members', nonMembers: 'Non-members',
    tickets: 'Tickets', attendee: 'Attendee', attendees: 'Attendees', name: 'Name', email: 'Email', phone: 'Phone',
    space: 'Space', title: 'Title', start: 'Start', end: 'End', days: 'Days', expected: 'Expected attendees',
    visibility: 'Visibility', public: 'Public', private: 'Private', newsletter: 'Newsletter', current: 'Current', next: 'New',
    unchanged: 'unchanged', staffEmail: 'Email to CCIFJ contact', teamTicket: 'Ticket for CCIFJ team',
    allTickets: 'All tickets on this booking will be cancelled.',
    generic: {title: 'Review this action', approve: 'Approve', reject: 'Don’t approve', note: 'Approving will run the action shown above.'},
    actions: {
      book_event_ticket: {title: 'Book event tickets', approve: 'Confirm booking', reject: 'Don’t book', note: 'Your ticket reservation will be created in your CCIFJ account. No payment will be taken.'},
      book_space: {title: 'Reserve a CCIFJ space', approve: 'Confirm reservation', reject: 'Don’t reserve', note: 'Confirming submits this reservation to CCIFJ.'},
      subscribe_newsletter: {title: 'Subscribe to a newsletter', approve: 'Subscribe', reject: 'Don’t subscribe', note: 'You will receive this newsletter at the email shown.'},
      save_introduction_request: {title: 'Introduction to a member', approve: 'Store for staff review', reject: 'Don’t store it', note: 'CCIFJ staff review every introduction before anything is sent to the member.'},
      save_member_followup: {title: 'Follow-up message to a member', approve: 'Store for review', reject: 'Don’t store it', note: 'The message is stored for staff review. Nothing is sent to the member automatically.'},
      submit_staff_request: {title: 'Message for CCIFJ staff', approve: 'Store my message', reject: 'Don’t store it', note: 'Your message is stored for the CCIFJ team. No email is sent automatically.'},
      cancel_event_booking: {title: 'Cancel booking', approve: 'Cancel booking', reject: 'Keep booking', note: 'This cannot be undone. You can book again while registration is open.'},
      cancel_space_reservation: {title: 'Cancel reservation', approve: 'Cancel reservation', reject: 'Keep reservation', note: 'This cannot be undone. The space becomes available to others.'},
      change_event_booking: {title: 'Change booking', approve: 'Confirm change', reject: 'Keep as is', note: 'Confirming updates your booking. No payment will be taken.'},
      change_space_reservation: {title: 'Change reservation', approve: 'Confirm change', reject: 'Keep as is', note: 'Confirming updates your reservation.'},
    } as Record<string, {title: string; approve: string; reject: string; note: string}>,
  },
  fr: {
    review: 'Vérifiez avant de confirmer', login: 'Connectez-vous pour approuver', detail: 'Détails de l’action', reference: 'Référence', event: 'Événement', eventId: 'ID de l’événement',
    eventDate: 'Date et heure', location: 'Lieu', price: 'Prix', free: 'Gratuit', members: 'Membres', nonMembers: 'Non-membres',
    tickets: 'Billets', attendee: 'Participant', attendees: 'Participants', name: 'Nom', email: 'E-mail', phone: 'Téléphone',
    space: 'Espace', title: 'Titre', start: 'Début', end: 'Fin', days: 'Jours', expected: 'Participants prévus',
    visibility: 'Visibilité', public: 'Public', private: 'Privé', newsletter: 'Newsletter', current: 'Actuel', next: 'Nouveau',
    unchanged: 'inchangé', staffEmail: 'E-mail à un contact CCIFJ', teamTicket: 'Ticket pour l’équipe CCIFJ',
    allTickets: 'Tous les billets de cette réservation seront annulés.',
    generic: {title: 'Vérifiez cette action', approve: 'Approuver', reject: 'Ne pas approuver', note: 'L’approbation exécutera l’action ci-dessus.'},
    actions: {
      book_event_ticket: {title: 'Réserver des billets', approve: 'Confirmer la réservation', reject: 'Ne pas réserver', note: 'La réservation sera enregistrée dans votre compte CCIFJ. Aucun paiement ne sera effectué.'},
      book_space: {title: 'Réserver un espace CCIFJ', approve: 'Confirmer la réservation', reject: 'Ne pas réserver', note: 'La confirmation transmet cette réservation à la CCIFJ.'},
      subscribe_newsletter: {title: 'S’abonner à une newsletter', approve: 'S’abonner', reject: 'Ne pas s’abonner', note: 'Vous recevrez cette newsletter à l’adresse indiquée.'},
      save_introduction_request: {title: 'Mise en relation avec un membre', approve: 'Enregistrer pour relecture', reject: 'Ne pas l’enregistrer', note: 'L’équipe CCIFJ relit chaque mise en relation avant tout envoi au membre.'},
      save_member_followup: {title: 'Message de suivi à un membre', approve: 'Enregistrer pour relecture', reject: 'Ne pas l’enregistrer', note: 'Le message est enregistré pour relecture. Rien n’est envoyé automatiquement au membre.'},
      submit_staff_request: {title: 'Message au personnel CCIFJ', approve: 'Enregistrer mon message', reject: 'Ne pas l’enregistrer', note: 'Votre message est enregistré pour l’équipe CCIFJ. Aucun e-mail n’est envoyé automatiquement.'},
      cancel_event_booking: {title: 'Annuler la réservation', approve: 'Annuler la réservation', reject: 'Garder la réservation', note: 'Cette action est définitive. Vous pourrez réserver à nouveau tant que les inscriptions sont ouvertes.'},
      cancel_space_reservation: {title: 'Annuler la réservation d’espace', approve: 'Annuler la réservation', reject: 'Garder la réservation', note: 'Cette action est définitive. L’espace redevient disponible.'},
      change_event_booking: {title: 'Modifier la réservation', approve: 'Confirmer la modification', reject: 'Ne rien changer', note: 'La confirmation met à jour votre réservation. Aucun paiement ne sera effectué.'},
      change_space_reservation: {title: 'Modifier la réservation d’espace', approve: 'Confirmer la modification', reject: 'Ne rien changer', note: 'La confirmation met à jour votre réservation.'},
    } as Record<string, {title: string; approve: string; reject: string; note: string}>,
  },
  ja: {
    review: '確定前にご確認ください', login: 'ログインして承認', detail: '操作の詳細', reference: '予約番号', event: 'イベント', eventId: 'イベントID',
    eventDate: '日時', location: '会場', price: '料金', free: '無料', members: '会員', nonMembers: '非会員',
    tickets: 'チケット枚数', attendee: '参加者', attendees: '参加者', name: '氏名', email: 'メールアドレス', phone: '電話番号',
    space: 'スペース', title: '名称', start: '開始', end: '終了', days: '日数', expected: '参加予定人数',
    visibility: '公開設定', public: '公開', private: '非公開', newsletter: 'ニュースレター', current: '現在', next: '変更後',
    unchanged: '変更なし', staffEmail: 'CCIFJ担当者へのメール', teamTicket: 'CCIFJチームへのチケット',
    allTickets: 'この予約のチケットはすべてキャンセルされます。',
    generic: {title: '操作内容の確認', approve: '承認', reject: '承認しない', note: '承認すると上記の操作が実行されます。'},
    actions: {
      book_event_ticket: {title: 'イベントチケットを予約', approve: '予約を確定', reject: '予約しない', note: '承認するとCCIFJアカウントにチケット予約が登録されます。支払いは発生しません。'},
      book_space: {title: 'CCIFJスペースを予約', approve: '予約を確定', reject: '予約しない', note: '確定するとこの予約がCCIFJに送信されます。'},
      subscribe_newsletter: {title: 'ニュースレターに登録', approve: '登録する', reject: '登録しない', note: '表示されたメールアドレスにニュースレターが届きます。'},
      save_introduction_request: {title: '会員へのご紹介', approve: '確認用に保存', reject: '保存しない', note: 'CCIFJスタッフが内容を確認してから会員に送ります。'},
      save_member_followup: {title: '会員へのフォローアップ', approve: '確認用に保存', reject: '保存しない', note: 'メッセージは確認用に保存されます。会員へ自動送信はされません。'},
      submit_staff_request: {title: 'CCIFJスタッフへのメッセージ', approve: 'メッセージを保存', reject: '保存しない', note: 'メッセージはCCIFJチーム向けに保存されます。メールは自動送信されません。'},
      cancel_event_booking: {title: '予約をキャンセル', approve: '予約をキャンセル', reject: '予約を維持', note: 'この操作は取り消せません。受付期間中は再度予約できます。'},
      cancel_space_reservation: {title: 'スペース予約をキャンセル', approve: '予約をキャンセル', reject: '予約を維持', note: 'この操作は取り消せません。スペースは他の方が予約できるようになります。'},
      change_event_booking: {title: '予約を変更', approve: '変更を確定', reject: '変更しない', note: '確定すると予約が更新されます。支払いは発生しません。'},
      change_space_reservation: {title: 'スペース予約を変更', approve: '変更を確定', reject: '変更しない', note: '確定すると予約が更新されます。'},
    } as Record<string, {title: string; approve: string; reject: string; note: string}>,
  },
};
type Words = typeof words.en;

function value(args: Args | undefined, ...names: string[]): unknown {
  const key = names.find(name => args?.[name] !== undefined && args?.[name] !== null && args?.[name] !== '');
  return key ? args?.[key] : undefined;
}

function text(args: Args | undefined, ...names: string[]): string | undefined {
  const found = value(args, ...names);
  return found === undefined ? undefined : String(found);
}

function record(found: unknown): Args | undefined {
  return typeof found === 'object' && found !== null && !Array.isArray(found) ? found as Args : undefined;
}

/** Schedules are naive Japan time; formatting the wall clock keeps them unchanged. */
function formatDate(iso: string | undefined, locale: Locale) {
  if (!iso) return undefined;
  const date = new Date(iso.replace(' ', 'T'));
  return Number.isNaN(date.getTime()) ? iso : new Intl.DateTimeFormat(locale, {dateStyle: 'medium', timeStyle: 'short'}).format(date);
}

function formatAmount(amount: unknown, currency: string | undefined, locale: Locale) {
  const number = Number(amount);
  if (!Number.isFinite(number)) return String(amount);
  try {
    return currency ? new Intl.NumberFormat(locale, {style: 'currency', currency}).format(number) : number.toLocaleString(locale);
  } catch {
    return `${number.toLocaleString(locale)} ${currency ?? ''}`.trim();
  }
}

function formatPrice(args: Args, locale: Locale, t: Words) {
  const currency = text(args, 'currency');
  const price = value(args, 'price');
  if (price !== undefined) return Number(price) === 0 ? t.free : formatAmount(price, currency, locale);
  const member = value(args, 'member_price'), nonMember = value(args, 'non_member_price');
  if (member === undefined && nonMember === undefined) return undefined;
  return [member !== undefined && `${t.members} ${Number(member) === 0 ? t.free : formatAmount(member, currency, locale)}`,
    nonMember !== undefined && `${t.nonMembers} ${Number(nonMember) === 0 ? t.free : formatAmount(nonMember, currency, locale)}`].filter(Boolean).join(' · ');
}

/** Calendar days covered by a reservation, counting both the first and last day. */
function dayCount(start: string | undefined, end: string | undefined) {
  if (!start || !end) return undefined;
  const first = new Date(start.slice(0, 10)), last = new Date(end.slice(0, 10));
  const days = Math.round((last.getTime() - first.getTime()) / 86_400_000) + 1;
  return Number.isFinite(days) && days > 0 ? days : undefined;
}

function people(found: unknown): Args[] {
  return Array.isArray(found) ? found.filter((item): item is Args => typeof item === 'object' && item !== null) : [];
}

function Field({label, children}: {label: string; children?: ReactNode}) {
  if (children === undefined || children === null || children === '') return null;
  return <div><span>{label}</span><strong dir="auto">{children}</strong></div>;
}

function Attendees({list, t}: {list: Args[]; t: Words}) {
  if (!list.length) return null;
  return <div className="approval-attendees">
    <div className="approval-section-label">{list.length > 1 ? t.attendees : t.attendee} <span>{list.length}</span></div>
    {list.map((person, index) => <div className="approval-person" key={`${String(person.email ?? person.name ?? index)}-${index}`}>
      <div className="approval-person-icon"><Ticket size={16}/></div>
      <div className="approval-person-fields">
        <Field label={t.name}>{text(person, 'name')}</Field>
        <Field label={t.email}>{text(person, 'email')}</Field>
        <Field label={t.phone}>{text(person, 'telephone', 'phone')}</Field>
      </div>
    </div>)}
  </div>;
}

function EventSummary({args, locale, t}: {args: Args; locale: Locale; t: Words}) {
  return <>
    {text(args, 'event_title') && <div className="approval-event-title" dir="auto">{text(args, 'event_title')}</div>}
    <div className="approval-event-meta">
      <Field label={t.eventDate}>{formatDate(text(args, 'event_start'), locale)}</Field>
      <Field label={t.location}>{text(args, 'event_location')}</Field>
    </div>
  </>;
}

function TicketDetails({args, locale, t}: {args: Args; locale: Locale; t: Words}) {
  const list = people(value(args, 'attendees', 'participants'));
  return <div className="approval-data">
    <EventSummary args={args} locale={locale} t={t}/>
    <div className="approval-event-meta">
      <Field label={t.price}>{formatPrice(args, locale, t)}</Field>
      <Field label={t.tickets}>{text(args, 'ticket_count') ?? (list.length || undefined)}</Field>
    </div>
    {text(args, 'event_id') && <div className="approval-event"><CalendarDays size={16}/><span>{t.eventId}</span><strong>{text(args, 'event_id')}</strong></div>}
    <Attendees list={list} t={t}/>
  </div>;
}

function SpaceDetails({args, locale, t}: {args: Args; locale: Locale; t: Words}) {
  const start = text(args, 'start_at', 'start_date'), end = text(args, 'end_at', 'end_date');
  const isPublic = value(args, 'is_public');
  return <div className="approval-data">
    <div className="approval-event-title" dir="auto">{text(args, 'space_name')}</div>
    <div className="approval-event-meta">
      <Field label={t.location}>{text(args, 'space_description')}</Field>
      <Field label={t.title}>{text(args, 'title')}</Field>
      <Field label={t.start}>{formatDate(start, locale)}</Field>
      <Field label={t.end}>{formatDate(end, locale)}</Field>
      <Field label={t.days}>{dayCount(start, end)}</Field>
      <Field label={t.expected}>{text(args, 'expected_attendees')}</Field>
      <Field label={t.visibility}>{isPublic === undefined ? undefined : isPublic === true ? t.public : t.private}</Field>
    </div>
  </div>;
}

function NewsletterDetails({args, t}: {args: Args; t: Words}) {
  return <div className="approval-data"><div className="approval-event-meta">
    <Field label={t.email}>{text(args, 'email')}</Field>
    <Field label={t.newsletter}>{text(args, 'newsletter_name', 'newsletter_type')}</Field>
  </div></div>;
}

function StaffRequestDetails({args, locale, t, label}: {args: Args; locale: Locale; t: Words; label?: string}) {
  const isTicket = text(args, 'request_type') === 'team_ticket';
  const recipients = Array.isArray(args.recipient_emails) ? args.recipient_emails.map(String).filter(Boolean) : [];
  const sender = [text(args, 'sender_name'), text(args, 'sender_email') && `<${text(args, 'sender_email')}>`].filter(Boolean).join(' ');
  return <DraftCard kind={isTicket ? 'ticket' : 'email'} label={label ?? (isTicket ? t.teamTicket : t.staffEmail)} locale={locale}
    fields={{to: recipients.join(', ') || undefined, from: sender || undefined, subject: text(args, 'subject'), body: text(args, 'message') ?? ''}}/>;
}

function CancelBookingDetails({args, locale, t}: {args: Args; locale: Locale; t: Words}) {
  return <div className="approval-data">
    <EventSummary args={args} locale={locale} t={t}/>
    <div className="approval-event-meta"><Field label={t.reference}>{text(args, 'booking_reference')}</Field></div>
    <Attendees list={people(args.attendees)} t={t}/>
    <p className="approval-warning">{t.allTickets}</p>
  </div>;
}

function CancelReservationDetails({args, locale, t}: {args: Args; locale: Locale; t: Words}) {
  return <div className="approval-data">
    <div className="approval-event-title" dir="auto">{text(args, 'space_name')}</div>
    <div className="approval-event-meta">
      <Field label={t.title}>{text(args, 'title')}</Field>
      <Field label={t.start}>{formatDate(text(args, 'start_at'), locale)}</Field>
      <Field label={t.end}>{formatDate(text(args, 'end_at'), locale)}</Field>
      <Field label={t.expected}>{text(args, 'expected_attendees')}</Field>
      <Field label={t.reference}>{text(args, 'reservation_reference')}</Field>
    </div>
  </div>;
}

type Row = {label: string; current?: string; next?: string};

/** One change shown as Current → New, with changed values highlighted. */
function ChangeTable({rows, t}: {rows: Row[]; t: Words}) {
  const shown = rows.filter(row => row.current !== undefined || row.next !== undefined);
  return <div className="approval-change" role="table" aria-label={`${t.current} → ${t.next}`}>
    <div className="approval-change-row approval-change-head" role="row">
      <span role="columnheader"/><span role="columnheader">{t.current}</span><ArrowRight size={14} aria-hidden="true"/><span role="columnheader">{t.next}</span>
    </div>
    {shown.map(row => {
      const changed = row.current !== row.next;
      return <div className={`approval-change-row${changed ? ' is-changed' : ''}`} role="row" key={row.label}>
        <span role="rowheader">{row.label}</span>
        <span role="cell" dir="auto">{row.current ?? '—'}</span>
        <ArrowRight size={14} aria-hidden="true"/>
        <strong role="cell" dir="auto">{changed ? row.next ?? '—' : t.unchanged}</strong>
      </div>;
    })}
  </div>;
}

function attendeeNames(found: unknown) {
  const list = people(found).map(person => text(person, 'name') ?? text(person, 'email')).filter(Boolean);
  return list.length ? list.join(', ') : undefined;
}

function ChangeBookingDetails({args, locale, t}: {args: Args; locale: Locale; t: Words}) {
  const current = record(args.current), next = record(args.new);
  if (!current || !next) return <RawDetails args={args} t={t}/>;
  return <div className="approval-data">
    <ChangeTable t={t} rows={[
      {label: t.event, current: text(current, 'event_title'), next: text(next, 'event_title')},
      {label: t.eventDate, current: formatDate(text(current, 'event_start'), locale), next: formatDate(text(next, 'event_start'), locale)},
      {label: t.location, current: text(current, 'event_location'), next: text(next, 'event_location')},
      {label: t.attendees, current: attendeeNames(current.attendees), next: attendeeNames(next.attendees)},
    ]}/>
    <Attendees list={people(next.attendees)} t={t}/>
  </div>;
}

function ChangeReservationDetails({args, locale, t}: {args: Args; locale: Locale; t: Words}) {
  const current = record(args.current), next = record(args.new);
  if (!current || !next) return <RawDetails args={args} t={t}/>;
  return <div className="approval-data">
    {text(current, 'space_name') && <div className="approval-event-title" dir="auto">{text(current, 'space_name')}</div>}
    <ChangeTable t={t} rows={[
      {label: t.title, current: text(current, 'title'), next: text(next, 'title')},
      {label: t.start, current: formatDate(text(current, 'start_at'), locale), next: formatDate(text(next, 'start_at'), locale)},
      {label: t.end, current: formatDate(text(current, 'end_at'), locale), next: formatDate(text(next, 'end_at'), locale)},
      {label: t.expected, current: text(current, 'expected_attendees'), next: text(next, 'expected_attendees')},
    ]}/>
  </div>;
}

function RawDetails({args, t}: {args: Args; t: Words}) {
  return <details className="approval-other-detail"><summary>{t.detail}</summary><pre>{JSON.stringify(args, null, 2)}</pre></details>;
}

function ActionDetails({action, locale, t}: {action: ApprovalAction; locale: Locale; t: Words}) {
  const args = action.args ?? {};
  switch (action.name) {
    case 'book_event_ticket': return <TicketDetails args={args} locale={locale} t={t}/>;
    case 'book_space': return <SpaceDetails args={args} locale={locale} t={t}/>;
    case 'subscribe_newsletter': return <NewsletterDetails args={args} t={t}/>;
    case 'submit_staff_request': return <StaffRequestDetails args={args} locale={locale} t={t}/>;
    case 'save_introduction_request':
    case 'save_member_followup': return <StaffRequestDetails args={args} locale={locale} t={t} label={(t.actions[action.name] ?? t.generic).title}/>;
    case 'cancel_event_booking': return <CancelBookingDetails args={args} locale={locale} t={t}/>;
    case 'cancel_space_reservation': return <CancelReservationDetails args={args} locale={locale} t={t}/>;
    case 'change_event_booking': return <ChangeBookingDetails args={args} locale={locale} t={t}/>;
    case 'change_space_reservation': return <ChangeReservationDetails args={args} locale={locale} t={t}/>;
    default: return <RawDetails args={args} t={t}/>;
  }
}

export function ApprovalPanel({
  approval,
  locale,
  authenticated,
  disabled,
  onLogin,
  onDecision,
}: {
  approval: PendingApproval;
  locale: Locale;
  authenticated: boolean;
  disabled: boolean;
  onLogin: () => void;
  onDecision: (decision: 'approve' | 'reject') => void;
}) {
  const t = words[locale];
  const names = [...new Set(approval.actions.map(action => action.name))];
  // One kind of action gets its own wording; a mixed set uses the generic wording.
  const copy = names.length === 1 ? t.actions[names[0]] ?? t.generic : t.generic;
  const destructive = names.length > 0 && names.every(name => CANCEL_ACTIONS.has(name));
  const canDecide = authenticated || (names.length > 0 && names.every(name => GUEST_ACTIONS.has(name)));
  return (
    <section className={`approval-panel${destructive ? ' is-destructive' : ''}`} aria-label={copy.title}>
      <div className="approval-topline"><span className="approval-icon"><ShieldCheck size={18}/></span><span className="approval-kicker">{t.review}</span></div>
      {approval.actions.map((action, index) => (
        <div className="approval-action" key={`${action.name}-${index}`}>
          <h3 className={CANCEL_ACTIONS.has(action.name) ? 'approval-danger' : undefined}>{(t.actions[action.name] ?? t.generic).title}</h3>
          <ActionDetails action={action} locale={locale} t={t}/>
        </div>
      ))}
      <p className="approval-explanation">{copy.note}</p>
      {canDecide ? (
        <div className="approval-buttons">
          <button type="button" className="approval-reject" disabled={disabled} onClick={() => onDecision('reject')}><X size={16}/>{copy.reject}</button>
          <button type="button" className={destructive ? 'approval-accept approval-destructive' : 'approval-accept'} disabled={disabled} onClick={() => onDecision('approve')}><Check size={16}/>{copy.approve}</button>
        </div>
      ) : (
        <button type="button" className="auth-button approval-login" disabled={disabled} onClick={onLogin}>{t.login}</button>
      )}
    </section>
  );
}
