# From prototype to CCIFJ production

What the hackathon brief asks every team to specify: how the solution fits the CCIFJ
environment (TYPO3, Odoo, GetResponse, optionally Prisme.ai), how staff keep it current
without a developer, what it costs to run, and who maintains what. Nothing below requires
changing the agent's code; integration points are listed with the file that implements them.

## 1. Architecture as deployed

```text
ccifj.or.jp (TYPO3)  ──embed──►  React chat widget  ──HTTPS──►  FastAPI API (one container)
                                                                 ├── SQLite (events, members, requests, contacts, knowledge docs)
                                                                 ├── Chroma + BM25 (website crawl + staff uploads)
                                                                 ├── OpenAI chat models (or Ollama/Qwen on-prem)
                                                                 └── Cohere rerank (optional)
CCIFJ staff  ──browser──►  Admin dashboard (same app, staff role)
```

One API process and one static frontend. No message queue, no external database. The
whole state is the `system.db` SQLite file and two index folders, so backup is a file copy.

## 2. Integration with the existing environment

| System | Role today | Integration path | Where |
|---|---|---|---|
| **TYPO3** (website) | Hosts ccifj.or.jp | Add one line to the page template or an HTML content element: `<script src="https://CONCIERGE-HOST/embed.js" data-lang="fr" defer></script>` (`ccifj-frontend/public/embed.js`, demo page `embed-demo.html`). It adds a chat button and opens the concierge in an iframe panel, sharing no cookies with the host page. The widget calls the API over HTTPS; set the site origin in `CCIFJ_CORS_ORIGINS`. Website content keeps feeding the knowledge base through the crawl (`src/concierge/data/documents.jsonl`), re-run when pages change. | `ccifj-frontend/`, `config.py` |
| **Odoo** (CRM) | Members, leads, invoices | Every confirmed visitor request is a `staff_request` row with recipient and reference, linked to the conversation it came from (staff read it under **Requests → Conversations**). **Implemented, off by default:** set `ODOO_URL`, `ODOO_DB`, `ODOO_USER`, `ODOO_API_KEY` and each approved request is also created in Odoo as a `crm.lead` through the standard XML-RPC API (reference in the lead name, visitor email as `email_from`, recipients in the description). The request is always stored locally first, so an Odoo outage loses nothing. Written against Odoo's documented API and tested with a simulated server; not yet run against CCIFJ's instance. Member list: export Odoo partners to CSV/XLSX and import them in the dashboard (**Members → Import**); the importer accepts the directory export layout as-is. | `integrations/odoo.py`, `imports.py` |
| **GetResponse** (mailing) | Newsletters | Newsletter opt-ins the agent records (`subscription` table) can be synced to a GetResponse list by its REST API on a nightly job. Onboarding messages (Agent 2) are drafts for staff review, not sent mail; sending through GetResponse is an opt-in step. | `engagement.py` |
| **Prisme.ai / Atlas** | CCIFJ's AI platform | Optional. The agent exposes a plain HTTP chat endpoint (`POST /api/v1/chat`) that an Atlas workflow can call as a tool, or the LangGraph graph can be hosted there. Not required. | `api/chat.py` |
| **Calameo / brochures** | Published PDFs | Upload once in **Knowledge** (dashboard) or with `scripts/ingest_documents.py`; add the Calameo link as the public source URL so citations point there. | `api/admin_content.py` |

Live integration was not part of the 3-day scope; the prototype runs standalone with
simulated connectors (requests and intros are stored, not emailed).

## 3. How staff update what the agent knows (no code)

| Change | Where in the dashboard | Effect |
|---|---|---|
| New brochure, price list, procedure | **Knowledge → Upload** (PDF, DOCX, text; scanned PDFs are OCR'd) | Searchable on the next question; cited as a source |
| Outdated document | **Knowledge → Delete** | Its passages stop appearing |
| Events | **Events** (edit) or **Import** CSV/XLSX | Event list, prices, deadlines |
| Member companies | **Members → Import** (Odoo export or template) | Directory search, matching, introductions |
| Who handles what | **Staff contacts** (department, "what they handle", email, triage flag) | The agent reads the whole directory and picks the department itself |
| Membership fees | `member_category` table (SQL today; a dashboard form is a small addition) | Fee answers use records, never web text |

The admin **Analytics** page shows usage, outcomes and fallbacks so staff see which
questions lack a source and can upload the missing document.

## 4. Deployment steps

1. A Linux VM or container (2 vCPU, 4 GB RAM is enough; the embedding model runs on CPU).
   Python 3.12+, Node for the one-time frontend build, Tesseract with `eng+fra+jpn`.
2. `cp .env.example .env`; set `CCIFJ_AUTH_SECRET`, model keys, `CCIFJ_CORS_ORIGINS`
   (the TYPO3 site), `CCIFJ_STAFF_USER_IDS` for the first staff account.
3. `./start.sh` (installs locked dependencies, migrates the DB, starts API + frontend).
   Behind nginx/Caddy for TLS. Keep one API worker (SQLite).
4. Load content: `scripts/ingest_documents.py <brochure folder>`, Members → Import,
   Events → Import, Staff contacts.
5. Nightly: copy `system.db`, `var/knowledge/`, `src/concierge/db/vectordb/` to backup.
   Re-run `scripts/verify.sh` after any dependency update.

## 5. Recurring costs (estimate at 1,000 conversations/month, ~4 turns each)

| Item | Basis | Monthly |
|---|---|---|
| Chat model | ~6k tokens per turn × 4,000 turns, GPT-class pricing | ~$5 |
| **Total** | | **~$5 / month** |

Running the chat model locally (Ollama/Qwen, already supported) removes the model line at
the cost of a GPU machine and lower answer quality. Costs scale linearly with turns.

## 6. Roles and maintenance

| Who | Responsibility |
|---|---|
| **CCIFJ staff** (Membership, SAE, Events, Communication) | Content owners: upload brochures, keep events/members imported, keep the staff directory current, review the staff inbox and intro requests, check Analytics weekly for unsourced answers |
| **CCIFJ IT / webmaster** | TYPO3 embed, DNS/TLS, backups, `.env` secrets, staff account IDs |
| **Developer team (hackathon team or 42 student)** | Dependency updates, model upgrades, prompt fixes, the Odoo/GetResponse connectors; ~1 day/month in steady state |
| **AI project lead at CCIFJ** (recommended, part-time) | Decides which sources are authoritative, signs off answer quality, owns the fallback policy |

Rules that change often (fees, categories, contacts, events) are data, edited in the
dashboard. Rules that change rarely (tone, scope, approval policy) are prompts in
`src/concierge/agents/schema_prompts.py`, edited by the developer.
