import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { ChatSource } from './types';
import { isSafeExternalUrl } from './api';

const markdownComponents: Components = {
  a({ href, children }) {
    if (!isSafeExternalUrl(href)) return <span>{children}</span>;
    return <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>;
  },
};

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
      let next = index + 1;
      while (next < lines.length && !lines[next].trim()) next += 1;
      const previousLine = compacted[compacted.length - 1] ?? '';
      const nextLine = lines[next] ?? '';
      const startsBlock = /^(\s{0,3}(#{1,6}\s|[-*+]\s|\d+[.)]\s|>\s|```|~~~|\|))/.test(nextLine);
      const followsBlock = /^(\s{0,3}(#{1,6}\s|[-*+]\s|\d+[.)]\s|>\s|```|~~~|\|))/.test(previousLine);
      if (startsBlock || followsBlock) compacted.push('');
      index = next - 1;
      continue;
    }

    compacted.push(line);
  }

  return compacted.join('\n').trim();
}

/** Render model answers as Markdown without enabling raw HTML. */
export function AnswerText({ text, sources = [] }: { text: string; sources?: ChatSource[] }) {
  const markdown = compactMarkdown(text).replace(/\[(\d+)\](?!\()/g, (citation, number: string) => {
    const source = sources[Number(number) - 1];
    return source && isSafeExternalUrl(source.url)
      ? `[${source.title}](${source.url})`
      : '';
  });
  const hasSafeLink = [...markdown.matchAll(/https:\/\/[^\s)>\]]+/g)]
    .some(([url]) => isSafeExternalUrl(url.replace(/[.,;:!?]+$/, '')));
  const primarySource = sources.find(source => isSafeExternalUrl(source.url));

  return (
    <div className="answer-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {markdown}
      </ReactMarkdown>
      {!hasSafeLink && primarySource && (
        <p className="answer-primary-source">
          <a href={primarySource.url} target="_blank" rel="noreferrer noopener">{primarySource.title}</a>
        </p>
      )}
    </div>
  );
}
