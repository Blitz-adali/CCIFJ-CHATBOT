import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { FollowUpQuestions } from './FollowUpQuestions';

describe('agent next actions', () => {
  it('renders at most two next actions as buttons beside follow-up questions', () => {
    const html = renderToStaticMarkup(<FollowUpQuestions locale="en" onSelect={() => {}} questions={['What is CCIFJ?']} nextActions={[
      { id: 'event.book', label: 'Book Networking Night', prompt: 'I want to book a ticket for event ID 12', event_id: 12 },
      { id: 'auth.login', label: 'Log in', prompt: '' },
      { id: 'contact.staff', label: 'Contact staff', prompt: 'Contact staff' },
    ]} />);
    expect(html).toContain('Book Networking Night');
    expect(html).toContain('Log in');
    expect(html).not.toContain('Contact staff');
    expect(html).toContain('What is CCIFJ?');
  });

  it('drops actions without a prompt to send', () => {
    const html = renderToStaticMarkup(<FollowUpQuestions locale="en" onSelect={() => {}} nextActions={[
      { id: 'membership.join', label: 'Join', prompt: ' ' },
    ]} />);
    expect(html).toBe('');
  });
});
