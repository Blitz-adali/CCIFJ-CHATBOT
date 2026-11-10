# Approved CCIFJ documents

Every `.md` file in this folder is part of what the concierge knows, next to
the crawled website pages. Answers that use a file cite it by its `title`.

To update what the concierge knows, no code is needed:

| I want to… | Do this |
|---|---|
| Add a brochure, rate card, or calendar | `uv run concierge-kb add "path/to/file.pdf"` (PDF, DOCX, TXT, MD, or a folder) |
| Correct a fact | Edit the `.md` file in any text editor, then `uv run concierge-kb sync` |
| Replace last year's version | `uv run concierge-kb remove "Old title"`, then `add` the new file |
| See what is indexed | `uv run concierge-kb list` |

Restart the API after `add`, `sync`, or `remove`.

Each file starts with a short header. `title` is the name shown to users as the
source, so keep it short and include the year:

```text
---
title: Media Kit FJE 2026
source_file: Media Kit FJE 2026.pdf
added: 2026-11-09
---
```

Text read from a PDF keeps the PDF's reading order, which can scramble tables.
After adding a file with prices, open the `.md` once and check that each price
sits next to what it is for.
