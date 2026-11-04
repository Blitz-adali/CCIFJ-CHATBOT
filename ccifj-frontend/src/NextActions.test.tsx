import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { NextActions } from './NextActions';

describe('concierge next steps', () => {
  it('renders an official action card in the selected language', () => {
    const html = renderToStaticMarkup(<NextActions locale="fr" actions={[{
      type: 'external_link', label: 'Découvrir l’adhésion', description: 'CCIFJ',
      url: 'https://www.ccifj.or.jp/en/membership.html',
    }, {
      type: 'external_link', label: 'Événements', url: 'https://www.ccifj.or.jp/en/events.html',
    }]} />);
    expect(html).toContain('Liens utiles');
    expect(html).toContain('Découvrir l’adhésion');
    expect(html).toContain('rel="noreferrer noopener"');
  });

  it('drops unsafe URLs and duplicate links and does not show submission claims', () => {
    const html = renderToStaticMarkup(<NextActions locale="en" actions={[
      { type: 'external_link', label: 'Unsafe', url: 'javascript:alert(1)' },
      { type: 'external_link', label: 'Membership', url: 'https://www.ccifj.or.jp/membership' },
      { type: 'external_link', label: 'Duplicate', url: 'https://www.ccifj.or.jp/membership' },
      { type: 'submit', label: 'Submitted', url: 'https://www.ccifj.or.jp/form' },
      { type: 'external_link', label: 'Events', url: 'https://www.ccifj.or.jp/events' },
    ]} />);
    expect(html).not.toContain('Unsafe');
    expect(html).not.toContain('Duplicate');
    expect(html).not.toContain('Submitted');
    expect(html.match(/<a /g)).toHaveLength(2);
  });

  it('shows nothing for a single link, which is already in the answer', () => {
    const html = renderToStaticMarkup(<NextActions locale="en" actions={[
      { type: 'external_link', label: 'Inquiry form', url: 'https://www.ccifj.or.jp/en/commercial-support/contact-us.html' },
    ]} />);
    expect(html).toBe('');
  });

  it('shows three links at a time and drops links with the same label', () => {
    const html = renderToStaticMarkup(<NextActions locale="en" actions={[
      { type: 'external_link', label: 'Events', url: 'https://www.ccifj.or.jp/en/events.html' },
      { type: 'external_link', label: 'Events', url: 'https://www.ccifj.or.jp/en/events/upcoming-events.html' },
      { type: 'external_link', label: 'Membership', url: 'https://www.ccifj.or.jp/en/membership.html' },
      { type: 'external_link', label: 'Membership again', url: 'https://www.ccifj.or.jp/en/membership.html/' },
      { type: 'external_link', label: 'Commercial support', url: 'https://www.ccifj.or.jp/en/commercial-support.html' },
      { type: 'external_link', label: 'Recruitment', url: 'https://www.ccifj.or.jp/en/recruitmenttraining.html' },
      { type: 'external_link', label: 'Publications', url: 'https://www.ccifj.or.jp/en/publications.html' },
    ]} />);
    expect(html.match(/<a /g)).toHaveLength(3);
    expect(html).toContain('Show 2 more');
    expect(html).not.toContain('Membership again');
  });
});
