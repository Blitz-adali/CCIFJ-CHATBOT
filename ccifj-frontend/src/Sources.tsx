import type { ChatSource, Locale } from './types';
import { isSafeExternalUrl } from './api';
import { getCopy } from './i18n';

/** The official pages and documents an answer cites, numbered like its [1] markers. */
export function Sources({ sources = [], locale }: { sources?: ChatSource[]; locale: Locale }) {
  // Cited sources come first in the list, so a marker's number is its position.
  const cited = sources
    .map((source, index) => ({ source, number: index + 1 }))
    .filter(({ source }) => source.cited && source.title && (!source.url || isSafeExternalUrl(source.url)));
  if (!cited.length) return null;
  const copy = getCopy(locale);
  return <section className="response-section answer-sources" aria-label={copy.sources}>
    <div className="section-label">{copy.sources}</div>
    <ol className="source-list">{cited.map(({ source, number }) =>
      <li key={`${number}-${source.url ?? source.title}`} value={number}>
        {source.url
          ? <a href={source.url} target="_blank" rel="noreferrer noopener" title={source.url}><span dir="auto">{source.title}</span><span aria-hidden="true"> ↗</span></a>
          : <span dir="auto">{source.title} <small>({copy.sourceDocument})</small></span>}
      </li>
    )}</ol>
  </section>;
}
