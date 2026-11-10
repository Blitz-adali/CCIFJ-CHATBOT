import type { ChatSource, Locale } from './types';
import { isSafeExternalUrl } from './api';

export function SourceList({sources = [], locale}: {sources?: ChatSource[]; locale: Locale}) {
  const evidence = sources.filter(source=>(source.provenance==='retrieved'||source.provenance==='database')&&isSafeExternalUrl(source.url));
  if (!evidence.length) return null;
  const label = locale==='fr'?'Sources consultées':locale==='ja'?'参照した情報源':'Sources used';
  return <details className="retrieved-sources"><summary>{label} · {evidence.length}</summary><ul>{evidence.map(source=><li key={source.url}><a href={source.url} target="_blank" rel="noreferrer noopener">{source.title}<span aria-hidden="true"> ↗</span></a></li>)}</ul></details>;
}
