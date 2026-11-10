import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { ChatSource, Locale } from './types';
import { isSafeExternalUrl } from './api';
import { DraftCard, parseDraft, type DraftKind } from './DraftCard';

const DOCUMENT_MARKER = '#ccifj-source-';

type HastNode = { type: string; tagName?: string; value?: string; properties?: { className?: unknown }; children?: HastNode[] };

function draftKind(language: string | undefined): DraftKind | undefined {
  return language === 'email' || language === 'ticket' ? language : undefined;
}

/** How many email/ticket fences the text has, and whether the last one is still open (streaming). */
function draftFences(text: string) {
  let open: string | null = null, language: string | undefined, count = 0;
  for (const line of text.split('\n')) {
    const match = line.match(/^\s*(```+|~~~+)\s*([\w-]*)/);
    if (!match) continue;
    if (!open) {
      open = match[1].slice(0, 3);
      language = match[2].toLowerCase();
      if (draftKind(language)) count += 1;
    } else if (match[1].startsWith(open) && !match[2]) open = null;
  }
  return { count, lastOpen: open !== null && Boolean(draftKind(language)) };
}

function markdownComponents(locale: Locale, drafts: { count: number; lastOpen: boolean }): Components {
  let seen = 0;
  return {
    a({ href, title, children }) {
      // A marker for an approved document that has no public page: same look, no link.
      if (href?.startsWith(DOCUMENT_MARKER)) return <sup className="citation"><span title={title} aria-label={`${children}: ${title}`}>{children}</span></sup>;
      if (!isSafeExternalUrl(href)) return <span>{children}</span>;
      // A numbered source marker: small, and it names its page on hover.
      if (typeof children === 'string' && /^\d{1,2}$/.test(children) && title) {
        return <sup className="citation"><a href={href} target="_blank" rel="noreferrer noopener" title={title} aria-label={`${children}: ${title}`}>{children}</a></sup>;
      }
      return <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>;
    },
    pre({ node, children }) {
      // ```email and ```ticket blocks are staff drafts: show a card, not code.
      const code = (node as HastNode | undefined)?.children?.[0];
      const classes = code?.tagName === 'code' && Array.isArray(code.properties?.className) ? code.properties.className.map(String) : [];
      const kind = draftKind(classes.find(name => name.startsWith('language-'))?.slice('language-'.length));
      if (!code || !kind) return <pre>{children}</pre>;
      seen += 1;
      const text = (code.children ?? []).map(child => child.value ?? '').join('');
      return <DraftCard kind={kind} fields={parseDraft(text)} locale={locale} drafting={drafts.lastOpen && seen === drafts.count}/>;
    },
  };
}

function compactMarkdown(text: string) {
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  const compacted: string[] = [];
  let codeFence: string | null = null;

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const fence = line.match(/^\s*(```+|~~~+)/)?.[1];
    if (fence) {
      if (!codeFence) codeFence = fence.slice(0, 3);
      else if (fence.startsWith(codeFence)) codeFence = null;
      compacted.push(line);
      continue;
    }

    if (!codeFence && !line.trim()) {
      // Collapse a run of blank lines into one: dropping it would merge two paragraphs.
      let next = index + 1;
      while (next < lines.length && !lines[next].trim()) next += 1;
      if (compacted.length && next < lines.length) compacted.push('');
      index = next - 1;
      continue;
    }

    compacted.push(line);
  }

  return compacted.join('\n').trim();
}

/** Render model answers as Markdown without enabling raw HTML. */
export function AnswerText({ text, sources = [], locale = 'en' }: { text: string; sources?: ChatSource[]; locale?: Locale }) {
  // "[2]" points to the second source. With a page it becomes a small link;
  // a document without a page shows the same number without a link; anything else is dropped.
  const markdown = compactMarkdown(text).replace(/[ \t]?\[(\d{1,2})\](?!\()/g, (citation, number: string) => {
    const source = sources[Number(number) - 1];
    if (!source || source.cited === false) return '';
    if (isSafeExternalUrl(source.url ?? undefined)) return `[${number}](${source.url} "${source.title.replace(/["\\\n]/g, ' ')}")`;
    return source.url ? '' : `[${number}](${DOCUMENT_MARKER}${number} "${source.title.replace(/["\\\n]/g, ' ')}")`;
  });
  const hasSafeLink = [...markdown.matchAll(/https:\/\/[^\s)>\]]+/g)]
    .some(([url]) => isSafeExternalUrl(url.replace(/[.,;:!?]+$/, '')));
  const hasCitedSources = sources.some(source => source.cited);
  const primarySource = hasCitedSources ? undefined : sources.find(source => isSafeExternalUrl(source.url ?? undefined));

  return (
    <div className="answer-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents(locale, draftFences(markdown))}>
        {markdown}
      </ReactMarkdown>
      {!hasSafeLink && primarySource && (
        <p className="answer-primary-source">
          <a href={primarySource.url ?? undefined} target="_blank" rel="noreferrer noopener">{primarySource.title}</a>
        </p>
      )}
    </div>
  );
}
