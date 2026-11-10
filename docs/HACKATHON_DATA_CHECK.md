# Agent implementation vs. the hackathon data pack

> **Status (9 Nov 2026, evening):** gaps 1, 2, 4 and the small items below are closed in
> this branch: the member directory now holds the organisers' 50 companies and their 116 contact
> people as separate records (`scripts/replace_members.py`); events are checked against their
> ccifj.or.jp pages (`scripts/refresh_events.py`); all 18 brochures are in the knowledge base
> (`scripts/ingest_documents.py`, 143 chunks, the two WIB PDFs via OCR); the contact tool
> returns the whole staff directory; superseded sources (2017 domiciliation, €480) are
> dropped at search time; retrieval also searches the user's original FR/JA wording.
> Still open: run `scripts/faq_check.py` against the live API (gap 3) and record language
> evidence; re-indexing the FR/JA crawl (gap 5) stays post-hackathon. Deployment, costs
> and ownership are in [DEPLOYMENT.md](DEPLOYMENT.md).

Checked 9 November 2026 against `C:\Users\User\Downloads\CCIFJ general info\`
(brief, kickoff slides, 18 brochures, 71-row FAQ scenario bank, 50 fictional
companies + 116 contacts). Static review of this checkout plus the local
database backup; no live model calls were made.

## What already lines up

| Brief requirement | Where it is implemented | Evidence |
|---|---|---|
| Understand intent, recommend a service, turn it into action | `agents/body.py` orchestrator → events / membership / general / collector nodes; `submit_staff_request`, bookings, intro requests | `schema_prompts.py` rules 1a, 5, 9a |
| Evidence-based answers, no fabrication | `search_vectordb` returns `Source: url / Chunk: id`; `used_source_ids` in the final schema; "Which source wins" rule (DB records beat web search) | `schema_prompts.py:196-203`, `agents/tools.py` |
| Honest fallback | tool returns "No relevant source passages found… offer CCIFJ staff help"; `recovery.py`, `test_failure_recovery.py` | |
| Membership fees | `member_category` table matches the March 2026 brochure exactly: Benefactor ¥650,000, Active ¥295,000, Startup ¥140,000, Individual ¥80,000, Regional/Non-Kanto ¥150,000, Non-Resident €550 (+ Associate ¥210,000) | DB backup `var/db-backup-2026-11-09/system.db` |
| Price conflict €480 vs €550 | DB is source of truth (€550); corpus still contains the 2017 domiciliation PDF with €480, but the prompt tells the model to prefer records and flag disagreement | `resources.py:MEMBERSHIP_FEE_SOURCE` |
| Routing to the right team | `contact_us` directory (Business Support, Membership, Recruitment & Training, Communication) + `get_ccifj_contact` topics + triage fallback | `staff_directory.py` |
| Staff edit without code | Admin dashboard: knowledge upload (PDF/DOCX/text, OCR fallback), event & member import, contact directory | `api/admin_content.py`, `api/attachments.py` |
| Member matching (Agent 3) | `matching.py` Chroma index over member descriptions, `find_matching_members`, `request_introduction`, staff review in `admin_matching.py` | 743 members in local DB |
| Onboarding / engagement (Agent 2) | `engagement.py`, `demo_engagement.py`, migration 0007 | |
| FR / EN / JA | UI locale, prompts answer in user language; retrieval query is rewritten to English | `search_vectordb` docstring |

## Gaps found

### 1. The hackathon member CSV does not import (blocking for the matching demo)

`EN_fictional_50_companies_varied_roles.csv` → `imports.read_table` +
`validate_member`: **0 of 166 rows valid, all "name is required"**.

- CSV headers normalise to `full_name, salutation, job_title, email, country,
  primary_sector, secondary_sector(s), membership_type, communication_language,
  display_profile_in_the_online_directory, description`.
- Importer expects `name, sector, category, country, region, continent,
  manpower, description, contact, established`.
- `membership_type` values ("Membership - Resident Active Member - Kanto rate",
  "… Outside Kanto rate", "Membership - Associate Member") don't match the
  category names (`Active`, `Non-Kanto`, `Associate`).
- The file mixes 50 company rows and 116 person rows ("COMPANY 01, Sophie
  DUBOIS"); the `Member` model has no person/contact rows, no
  `communication_language`, no directory-visibility flag.

Fix: add header aliases (`full_name→name`, `primary_sector→sector`,
`membership_type→category`, `email→contact`), map the three membership strings
to categories, skip or attach rows whose `full_name` contains a comma (people),
and store `display_profile` so the hidden-profile case can be tested.

### 2. The 18 supplied brochures are not in the knowledge base

The shipped corpus (`data/chunks.json`, 2,325 chunks) is a website crawl.
454 chunks come from site PDFs, but the 2026 brochures in the data pack are
**not** among them: membership brochure (Mar 2026), domiciliation #3 (2026 —
only the 2017 FR version is indexed), Missions 2026 (only Tremplin and Gateway
partially), 2026 training calendar (1 chunk mentions it), media kits / FJE
sponsorship rates (2 chunks), Display your products, WIB, services synthesis.
Term coverage: "Business Starter" 0 chunks, "Expo+" 3, "Business Connect" 7.

The local DB backup predates migration 0005, so there is no evidence any
brochure was uploaded through the knowledge upload yet. The upload path exists
(PDF + OCR) — it just has to be exercised, one copy per brochure (the ZIP and
`DOCUMENT/` folder are byte-identical duplicates of `Facts your agent can cite/`).
Skip `WIB ENG/JP` until OCR'd (text extraction yields 11 chars).

### 3. FAQ scenario bank is not used

The 71 bilingual scenarios (`faq-scenarios.json`) are not in the repo. The
built-in `predefined_qa.json` has 15 generic Q&As. The FAQ's biggest buckets —
**Account & Member Area** (lost login, change e-mail, reset password, directory
access: 7 rows), **Billing & Payment** (invoices, receipts, refunds: 5),
**Venue rental** (price, floor plan, room dimensions: 3), **Employer of Record**
(4), **Translation/Interpretation** (3) — have thin corpus coverage:
unsubscribe 1 chunk, login/password 6, training 2026 1, ad rates 2.

These are also the judge's likely test set (target 19/20). Suggested: load the
FAQ as a judge checklist (`tests/concierge_*_challenges.json` format) and add
approved answers for the account/billing rows via the knowledge upload.

### 4. Routing topics miss several FAQ departments

FAQ routing labels: Membership 20, SAE 13, Events 5, General 5, Communications
4, Payroll/SAE 4, Employment/HR 4, Training 3, Venue Rental 3, Accounting 4.
`staff_directory.TOPICS` has no `training`, `accounting/billing`,
`venue_rental` or `communications` topic, so those fall to `general_or_unsure`.
The `contact_us` table does have Communication and Recruitment & Training
rows, so it is a topic-key mapping gap, not a data gap.

### 5. Multilingual retrieval is English-only at index time

`rag/src/index_init.py` keeps only `language in (en, unknown)` pages; FR/JA
pages (4,606 records in `documents.jsonl`) are dropped. Answers in FR/JA work
by rewriting the query to English and translating the answer, but citations
will always point to English pages and FR/JA-only content (e.g. Plaquette JP,
JP media kit) is unreachable. Acceptable for the demo; say so in the handover.

### 6. Small items

- `config/secret_key` is committed in `f33af31`. Remove from git, add to
  `.gitignore`, rotate.
- Backend tests: `uv run python -m unittest discover -s tests -p 'test_*.py'`
  → **195 of 197 pass**. The 2 errors are Windows-only: `test_challenge_runner.py:71`
  and `test_concierge_quality.py:65` call `read_text()` without
  `encoding="utf-8"`, so cp1252 chokes on the JSON fixtures. Not a product bug.
- Kickoff slides: submission freeze **Wed 11 Nov 10:30 JST**, presentations
  14:05. Handover must list repo + final commit, demo launch steps, reviewer
  access, test evidence, language evidence, sources, AI/service declarations,
  recurring costs and maintenance owner (`docs/HACKATHON.md` covers most; costs
  and owner are missing).

## Priority before Wednesday

1. Fix the member import aliases and load the 50 companies (unblocks the
   matching demo with the organisers' own data).
2. Upload the 2026 membership, domiciliation, missions, business-centers and
   training-calendar brochures through the staff knowledge upload; verify one
   answer cites them.
3. Add `training`, `billing`, `venue_rental`, `communications` to
   `staff_directory.TOPICS` and map them to the existing contacts.
4. Run 20 FAQ scenarios (EN/FR/JA mix) through the live API and record
   pass/fail + the 5 fallback cases (missing price, conflicting price,
   nonexistent member, unavailable event, unsupported policy).
