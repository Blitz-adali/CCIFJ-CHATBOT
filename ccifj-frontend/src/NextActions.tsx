import type { ChatAction, Locale } from './types';
import { isSafeExternalUrl } from './api';
import { getCopy } from './i18n';

/** Show actionable official links without exposing internal workflow metadata. */
export function NextActions({ actions = [], locale }: { actions?: ChatAction[]; locale: Locale }) {
  const seen = new Set<string>();
  const links = actions.filter(action => {
    if (action.type !== 'external_link' || !isSafeExternalUrl(action.url) || seen.has(action.url)) return false;
    seen.add(action.url);
    return true;
  });
  if (!links.length) return null;
  return <section className="response-section" aria-label={getCopy(locale).actions}>
    <div className="section-label">{getCopy(locale).actions}</div>
    <div className="action-list">{links.map(action =>
      <a className="action-card next-action-link" key={action.url} href={action.url} target="_blank" rel="noreferrer noopener">
        <div className="action-content"><span className="action-heading">{action.label}</span>
          {action.description && <p>{action.description}</p>}
        </div><span aria-hidden="true">↗</span>
      </a>
    )}</div>
  </section>;
}
