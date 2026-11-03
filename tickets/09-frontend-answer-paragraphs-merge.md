# 09 · Answer paragraphs run together in the chat

**Area:** Frontend (`ccifj-frontend/src/AnswerText.tsx`) · **Priority:** Medium

## Problem
Separate paragraphs in an agent answer show up joined on one line. Example from "Who can I talk to about CCIFJ membership?":

```
+81 (0)3-4500-6644 Marianne Barges
...
+81 (0)3-4500-6522 I can help you draft an email to either of them.
```

The API reply is correct: each contact is its own paragraph, separated by a blank line:

```
**Tomoyo Funabashi**  
Membership Manager  
Email: t.funabashi@ccifj.or.jp  
Phone: +81 3-4500-6644

**Marianne Barges**  
...
```

## Cause
`compactMarkdown()` (around line 48) removes every blank line unless the next or previous line starts a heading, list, quote, table or code fence:

```ts
if (startsBlock || followsBlock) compacted.push('');
```

Without the blank line, Markdown treats the two paragraphs as one. The last line of the first paragraph (which has no trailing double-space) joins the first line of the next.

## Fix
Collapse runs of blank lines into **one**, instead of dropping them:

```ts
if (!codeFence && !line.trim()) {
  let next = index + 1;
  while (next < lines.length && !lines[next].trim()) next += 1;
  if (compacted.length && next < lines.length) compacted.push('');
  index = next - 1;
  continue;
}
```

If tighter spacing was the goal, reduce the paragraph margin in `widget.css` (`.widget-bubble p { margin: 0 0 6px; }`) instead of removing paragraph breaks.

Add a test in `AnswerText.test.tsx`: two paragraphs separated by one blank line render as two `<p>` elements.

## Acceptance
- Contact lists, the closing offer sentence, and any two paragraphs render as separate paragraphs.
- Lists, headings, and draft cards look the same as before.
