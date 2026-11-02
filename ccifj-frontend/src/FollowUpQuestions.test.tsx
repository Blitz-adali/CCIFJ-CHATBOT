import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { FollowUpQuestions } from './FollowUpQuestions';

describe('agent next actions', () => {
  it('shows at most three buttons, actions first', () => {
    const html = renderToStaticMarkup(<FollowUpQuestions locale="en" onSelect={() => {}} questions={['What is CCIFJ?', 'How do I join?']} nextActions={[
      { id: 'event.book', label: 'Book Networking Night', prompt: 'I want to book a ticket for event ID 12', event_id: 12 },
      { id: 'auth.login', label: 'Log in', prompt: '' },
      { id: 'contact.staff', label: 'Contact staff', prompt: 'Contact staff' },
    ]} />);
    expect(html).toContain('Book Networking Night');
    expect(html).toContain('Log in');
    expect(html).not.toContain('Contact staff');
    expect(html).toContain('What is CCIFJ?');
    expect(html.match(/<button /g)).toHaveLength(3);
  });

  it('lets labels pick their own text direction and styles actions apart from questions', () => {
    const html = renderToStaticMarkup(<FollowUpQuestions locale="en" onSelect={() => {}} questions={['What is CCIFJ?']} nextActions={[
      { id: 'events.browse', label: 'تصفح الفعاليات', prompt: 'أرني الفعاليات القادمة' },
    ]} />);
    expect(html).toContain('<span dir="auto">تصفح الفعاليات</span>');
    expect(html).toContain('class="next-action"');
    expect(html).toContain('class="follow-up-question"');
    expect(html.indexOf('next-action')).toBeLessThan(html.indexOf('follow-up-question'));
  });

  it('drops actions without a prompt to send', () => {
    const html = renderToStaticMarkup(<FollowUpQuestions locale="en" onSelect={() => {}} nextActions={[
      { id: 'membership.join', label: 'Join', prompt: ' ' },
    ]} />);
    expect(html).toBe('');
  });
});
