import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { NextActions } from './NextActions';

describe('concierge next steps', () => {
  it('renders an official action card in the selected language', () => {
    const html = renderToStaticMarkup(<NextActions locale="fr" actions={[{
      type: 'external_link', label: 'Découvrir l’adhésion', description: 'CCIFJ',
      url: 'https://www.ccifj.or.jp/en/membership.html',
    }]} />);
    expect(html).toContain('Prochaines étapes suggérées');
    expect(html).toContain('Découvrir l’adhésion');
    expect(html).toContain('rel="noreferrer noopener"');
  });

  it('drops unsafe URLs and duplicate links and does not show submission claims', () => {
    const html = renderToStaticMarkup(<NextActions locale="en" actions={[
      { type: 'external_link', label: 'Unsafe', url: 'javascript:alert(1)' },
      { type: 'external_link', label: 'Membership', url: 'https://www.ccifj.or.jp/membership' },
      { type: 'external_link', label: 'Duplicate', url: 'https://www.ccifj.or.jp/membership' },
      { type: 'submit', label: 'Submitted', url: 'https://www.ccifj.or.jp/form' },
    ]} />);
    expect(html).not.toContain('Unsafe');
    expect(html).not.toContain('Duplicate');
    expect(html).not.toContain('Submitted');
    expect(html.match(/<a /g)).toHaveLength(1);
  });
});
