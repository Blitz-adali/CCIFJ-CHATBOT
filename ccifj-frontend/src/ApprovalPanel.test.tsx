import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ApprovalPanel } from './ApprovalPanel';
import type { Locale } from './types';

function render(name: string, args: Record<string, unknown>, locale: Locale = 'en', authenticated = true) {
  return renderToStaticMarkup(<ApprovalPanel locale={locale} authenticated={authenticated} disabled={false} onLogin={() => {}} onDecision={() => {}}
    approval={{ interrupt_id: 'i', actions: [{ name, args }] }}/>);
}

describe('approval cards', () => {
  it('shows every saved value of a ticket booking with action-specific buttons', () => {
    const html = render('book_event_ticket', {
      event_id: '12', event_title: 'Tokyo Summit', event_start: '2026-11-20T18:00:00', event_location: 'Hotel Okura',
      price: '5000.00', currency: 'JPY', ticket_count: 2,
      attendees: [{ name: 'Jane', email: 'jane@example.com', telephone: '090' }, { name: 'Ken', email: 'ken@example.com' }],
    });
    for (const text of ['Tokyo Summit', 'Hotel Okura', '5,000', 'jane@example.com', 'Ken', 'Confirm booking', 'Don’t book']) expect(html).toContain(text);
    expect(html).not.toContain('<pre>');
    expect(render('book_event_ticket', { event_title: 'Free talk', price: '0' })).toContain('Free');
  });

  it('describes space bookings, newsletters, and staff requests in the UI language', () => {
    const space = render('book_space', { space_name: 'L’Espace', space_description: 'Iidabashi 2F', title: 'Board', start_at: '2026-12-01T09:00:00', end_at: '2026-12-02T18:00:00' }, 'fr');
    for (const text of ['L’Espace', 'Iidabashi 2F', 'Jours', '>2<', 'Confirmer la réservation']) expect(space).toContain(text);
    const news = render('subscribe_newsletter', { email: 'a@example.com', newsletter_name: '@StudioCCIFJ Podcast Newsletter' }, 'ja');
    expect(news).toContain('@StudioCCIFJ Podcast Newsletter');
    expect(news).toContain('登録する');
    const staff = render('submit_staff_request', {
      request_type: 'staff_email', recipient_emails: ['e.suzuki@ccifj.or.jp'], sender_name: 'Jane', sender_email: 'jane@example.com',
      subject: 'Late registration', message: 'Dear Ms Suzuki,\n\nCould I still register?',
    }, 'en', false);
    for (const text of ['Email to CCIFJ contact', 'e.suzuki@ccifj.or.jp', 'Jane &lt;jane@example.com&gt;', 'Late registration', 'Could I still register?', 'Store my message']) expect(staff).toContain(text);
    expect(staff).not.toContain('Log in to approve');
    expect(render('submit_staff_request', { request_type: 'team_ticket', subject: 's', message: 'm' })).toContain('Ticket for CCIFJ team');
  });

  it('asks guests to log in for account actions', () => {
    const html = render('book_space', { space_name: 'Room' }, 'en', false);
    expect(html).toContain('Log in to approve');
    expect(html).not.toContain('Confirm reservation');
  });

  it('shows a cancellation in red with everything that will be cancelled', () => {
    const html = render('cancel_event_booking', {
      booking_reference: 'CCIFJ-EVT-00012', event_title: 'Gala', event_start: '2026-11-05T18:00:00',
      attendees: [{ name: 'Jane', email: 'jane@example.com' }],
    });
    for (const text of ['is-destructive', 'approval-danger', 'Cancel booking', 'Keep booking', 'All tickets on this booking will be cancelled.', 'Gala', 'Jane']) expect(html).toContain(text);
    expect(render('cancel_space_reservation', { space_name: 'Room A', title: 'Board' }, 'fr')).toContain('Garder la réservation');
  });

  it('shows a change as one current → new change without cancel wording', () => {
    const html = render('change_event_booking', {
      booking_reference: 'CCIFJ-EVT-00012',
      current: { event_title: 'Old talk', event_start: '2026-11-10T14:00:00', event_location: 'Tokyo', attendees: [{ name: 'Jane' }] },
      new: { event_title: 'New talk', event_start: '2026-11-12T14:00:00', event_location: 'Tokyo', attendees: [{ name: 'Jane' }] },
    });
    for (const text of ['Change booking', 'Current', 'New talk', 'Old talk', 'is-changed', 'unchanged', 'Confirm change', 'Keep as is']) expect(html).toContain(text);
    expect(html.toLowerCase()).not.toContain('cancel');
    const moved = render('change_space_reservation', {
      current: { space_name: 'Room A', title: 'Board', start_at: '2026-11-12T10:00:00', end_at: '2026-11-12T12:00:00', expected_attendees: 6 },
      new: { space_name: 'Room A', title: 'Board meeting', start_at: '2026-11-12T10:00:00', end_at: '2026-11-12T12:00:00', expected_attendees: 6 },
    }, 'ja');
    expect(moved).toContain('Board meeting');
    expect(moved).toContain('変更を確定');
  });

  it('keeps raw arguments only for unknown actions', () => {
    expect(render('mystery_tool', { x: 1 })).toContain('<pre>');
  });
});
