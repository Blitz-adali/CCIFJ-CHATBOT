# Approved CCIFJ documents (text versions)

Each `.md` file in this folder is the text of one CCIFJ brochure, rate card or
calendar supplied for the hackathon, kept in git so a fresh checkout can load
them without the original PDFs. They are **source files**: the concierge only
uses a document once it has been added to the knowledge base.

## Load them into the knowledge base

```bash
uv run python scripts/ingest_documents.py src/concierge/data/documents
```

The script reads every file, skips any that is already in the knowledge base
(same content), stores it as a knowledge document and indexes it for search.
Staff can then see, replace or delete each document in **Admin → Knowledge**.
The original PDFs can be loaded the same way (`scripts/ingest_documents.py
"path/to/folder"`) or uploaded one by one in the dashboard; load either the PDF
or its `.md` version, not both, or the same brochure is indexed twice.

## Updating a document

| I want to… | Do this |
|---|---|
| Add a brochure, rate card, or calendar | Upload it in **Admin → Knowledge**, or run the script on the file or folder |
| Correct a fact | Delete the document in the dashboard, edit the `.md` file, run the script again |
| Replace last year's version | Delete the old document in the dashboard, then add the new file |
| See what is indexed | **Admin → Knowledge** lists every document with its size |

No restart is needed: the next question searches the updated knowledge base.

Each file starts with a short header. `title` is the name of the brochure:

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
