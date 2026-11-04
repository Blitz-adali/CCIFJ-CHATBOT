import { useState } from 'react';
import type { ChatAction, Locale } from './types';
import { isSafeExternalUrl } from './api';
import { getCopy } from './i18n';

/** Links shown before "Show more"; the rest stay one click away. */
const VISIBLE_LINKS = 3;

function linkKey(url: string) {
  return url.replace(/#.*$/, '').replace(/\/+$/, '').toLowerCase();
}

/** Show actionable official links without exposing internal workflow metadata. */
export function NextActions({ actions = [], locale }: { actions?: ChatAction[]; locale: Locale }) {
  const [expanded, setExpanded] = useState(false);
  const seenUrls = new Set<string>(), seenLabels = new Set<string>();
  const links = actions.filter(action => {
    if (action.type !== 'external_link' || !isSafeExternalUrl(action.url)) return false;
    // The same page twice, or two links the user cannot tell apart, show once.
    const url = linkKey(action.url!), label = action.label.trim().toLowerCase();
    if (seenUrls.has(url) || seenLabels.has(label)) return false;
    seenUrls.add(url); seenLabels.add(label);
    return true;
  });
  // Every link also appears in the answer text; a single one needs no repeat.
  if (links.length < 2) return null;
  const copy = getCopy(locale);
  const hidden = links.length - VISIBLE_LINKS;
  const shown = expanded || hidden <= 0 ? links : links.slice(0, VISIBLE_LINKS);
  return <section className="response-section" aria-label={copy.actions}>
    <div className="section-label">{copy.actions}</div>
    <div className="link-chips">{shown.map(action =>
      <a className="link-chip" key={action.url} href={action.url} target="_blank" rel="noreferrer noopener" title={action.description || action.url}>
        <span dir="auto">{action.label}</span><span aria-hidden="true">↗</span>
      </a>
    )}
      {hidden > 0 && <button type="button" className="link-chip link-chip-more" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>
        {expanded ? copy.fewerLinks : copy.moreLinks.replace('{count}', String(hidden))}
      </button>}
    </div>
  </section>;
}
