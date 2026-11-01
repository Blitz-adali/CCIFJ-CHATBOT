import {CalendarDays, Check, ShieldCheck, Ticket, X} from 'lucide-react';
import type {ApprovalAction, Locale, PendingApproval} from './types';

const words = {
  en: {
    title: 'Review before booking',
    login: 'Log in to approve',
    approve: 'Confirm booking',
    reject: 'Don’t book',
    detail: 'Booking details',
    event: 'Event ID',
    eventDate: 'Date and time',
    location: 'Location',
    attendee: 'Attendee',
    attendees: 'Attendees',
    name: 'Name',
    email: 'Email',
    phone: 'Phone',
    noPayment: 'Your ticket reservation will be created in your CCIFJ account. No payment will be taken.',
    generic: 'Approving will submit the action shown below.',
  },
  fr: {
    title: 'Vérifiez avant de réserver',
    login: 'Connectez-vous pour approuver',
    approve: 'Confirmer la réservation',
    reject: 'Ne pas réserver',
    detail: 'Détails de la réservation',
    event: 'ID de l’événement',
    eventDate: 'Date et heure',
    location: 'Lieu',
    attendee: 'Participant',
    attendees: 'Participants',
    name: 'Nom',
    email: 'E-mail',
    phone: 'Téléphone',
    noPayment: 'La réservation sera enregistrée dans votre compte CCIFJ. Aucun paiement ne sera effectué.',
    generic: 'L’approbation exécutera l’action ci-dessous.',
  },
  ja: {
    title: '予約内容の確認',
    login: 'ログインして承認',
    approve: '予約を確定',
    reject: '予約しない',
    detail: '予約内容',
    event: 'イベントID',
    eventDate: '日時',
    location: '会場',
    attendee: '参加者',
    attendees: '参加者',
    name: '氏名',
    email: 'メールアドレス',
    phone: '電話番号',
    noPayment: '承認するとCCIFJアカウントにチケット予約が登録されます。支払いは発生しません。',
    generic: '承認すると、以下の操作が実行されます。',
  },
};

function actionTitle(action: ApprovalAction, locale: Locale) {
  const labels: Record<string, Record<Locale, string>> = {
    book_event_ticket: {en: 'Book event tickets', fr: 'Réserver des billets', ja: 'イベントチケットを予約'},
    book_space: {en: 'Reserve a CCIFJ space', fr: 'Réserver un espace CCIFJ', ja: 'CCIFJスペースを予約'},
    subscribe_newsletter: {en: 'Subscribe to the newsletter', fr: 'S’abonner à la newsletter', ja: 'ニュースレターに登録'},
  };
  return labels[action.name]?.[locale] ?? action.name.replaceAll('_', ' ');
}

function readArg(args: Record<string, unknown>, ...names: string[]) {
  const key = names.find(name => args[name] !== undefined && args[name] !== null && args[name] !== '');
  return key ? args[key] : undefined;
}

function BookingDetails({action, locale}: {action: ApprovalAction; locale: Locale}) {
  const t = words[locale];
  const eventId = readArg(action.args, 'event_id', 'eventId');
  const eventTitle = readArg(action.args, 'event_title', 'eventTitle');
  const eventDate = readArg(action.args, 'event_start', 'eventStart');
  const location = readArg(action.args, 'event_location', 'eventLocation');
  const attendees = readArg(action.args, 'attendees', 'participants');
  const people = Array.isArray(attendees) ? attendees.filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null) : [];

  return <div className="approval-data">
    {eventTitle !== undefined && <div className="approval-event-title">{String(eventTitle)}</div>}
    {(eventDate !== undefined || location !== undefined) && <div className="approval-event-meta">
      {eventDate !== undefined && <div><span>{t.eventDate}</span><strong>{String(eventDate).replace('T', ' ')}</strong></div>}
      {location !== undefined && <div><span>{t.location}</span><strong>{String(location)}</strong></div>}
    </div>}
    {eventId !== undefined && <div className="approval-event"><CalendarDays size={16}/><span>{t.event}</span><strong>{String(eventId)}</strong></div>}
    {people.length > 0 && <div className="approval-attendees">
      <div className="approval-section-label">{people.length > 1 ? t.attendees : t.attendee} <span>{people.length}</span></div>
      {people.map((person, index) => <div className="approval-person" key={`${String(person.email ?? person.name ?? index)}-${index}`}>
        <div className="approval-person-icon"><Ticket size={16}/></div>
        <div className="approval-person-fields">
          {person.name != null && <div><span>{t.name}</span><strong>{String(person.name)}</strong></div>}
          {person.email != null && <div><span>{t.email}</span><strong>{String(person.email)}</strong></div>}
          {person.telephone != null && <div><span>{t.phone}</span><strong>{String(person.telephone)}</strong></div>}
        </div>
      </div>)}
    </div>}
    {people.length === 0 && eventId === undefined && <details className="approval-other-detail">
      <summary>{t.detail}</summary><pre>{JSON.stringify(action.args, null, 2)}</pre>
    </details>}
  </div>;
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
  const isTicketBooking = approval.actions.some(action => action.name === 'book_event_ticket');
  return (
    <section className="approval-panel" aria-label={t.title}>
      <div className="approval-topline"><span className="approval-icon"><ShieldCheck size={18}/></span><span className="approval-kicker">{t.title}</span></div>
      {approval.actions.map((action, index) => (
        <div className="approval-action" key={`${action.name}-${index}`}>
          <h3>{actionTitle(action, locale)}</h3>
          {action.name === 'book_event_ticket'
            ? <BookingDetails action={action} locale={locale}/>
            : <details className="approval-other-detail"><summary>{t.detail}</summary><pre>{JSON.stringify(action.args, null, 2)}</pre></details>}
        </div>
      ))}
      <p className="approval-explanation">{isTicketBooking ? t.noPayment : t.generic}</p>
      {authenticated ? (
        <div className="approval-buttons">
          <button type="button" className="approval-reject" disabled={disabled} onClick={() => onDecision('reject')}><X size={16}/>{t.reject}</button>
          <button type="button" className="approval-accept" disabled={disabled} onClick={() => onDecision('approve')}><Check size={16}/>{t.approve}</button>
        </div>
      ) : (
        <button type="button" className="auth-button approval-login" disabled={disabled} onClick={onLogin}>{t.login}</button>
      )}
    </section>
  );
}
