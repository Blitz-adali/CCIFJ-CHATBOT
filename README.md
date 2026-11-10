# CCIFJ Concierge

An AI concierge for the **Chambre de Commerce et d'Industrie France Japon (CCIFJ)**.
Website visitors ask it about CCIFJ events, membership, business support, recruitment and
training, in English, French or Japanese. It answers from CCIFJ's own content, cites its
sources, and can take real actions, such as booking an event or writing a request to CCIFJ
staff, but only after the user approves them. Staff follow up and keep it current from a
dashboard, without coding.

| I want to… | Go to |
|---|---|
| Run it on my machine | [Quick start](#quick-start) |
| Try it as a visitor, a member or staff | [How to use it](#how-to-use-it) |
| See what it can do | [What the concierge can do](#what-the-concierge-can-do) |
| Review the hackathon submission | [Hackathon submission](#hackathon-submission) |
| Understand how it is built | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Install it for CCIFJ, and see the costs | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| Rehearse the demo | [docs/HACKATHON.md](docs/HACKATHON.md) |

---

## Quick start

### What you need

- Python **3.12 or later** and [uv](https://docs.astral.sh/uv/)
- Node.js and npm
- An **OpenAI API key**
- Optional: a Cohere API key (better search ranking), and Tesseract with English, French and
  Japanese language data (only for reading scanned PDFs and images)

### Start it

1. Copy the settings file:

   ```bash
   cp .env.example .env
   ```

2. Open `.env` and fill in two values: `OPENAI_API_KEY`, and `CCIFJ_AUTH_SECRET`, which you
   generate with:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

3. Start everything:

   ```bash
   ./start.sh
   ```

   The script installs the dependencies, prepares the database and starts the API on port 8001
   and the website on port 5173. It is a bash script: use macOS, Linux, WSL or Git Bash.

4. Open **http://localhost:5173**.

The agent loads its models in the background when the API starts (about 12–18 seconds, longer
the first time because the search model is downloaded). A message sent before that simply waits.

### Start it by hand (or in Windows PowerShell)

Do steps 1 and 2 above first, then:

```bash
uv sync
uv run concierge-db upgrade
uv run uvicorn concierge.main:app --app-dir src --reload
```

In a second terminal:

```bash
cd ccifj-frontend
npm ci
npm run dev
```

Open **http://127.0.0.1:5173**. Started this way, the API listens on port 8000.

### Demo accounts

You can chat without an account. To see the member and staff sides, sign in with one of these.
All four use the password `ccifj-demo-2026`.

| Account | What it shows |
|---|---|
| `staff@demo.ccifj.test` | The staff dashboard |
| `claire.member@demo.ccifj.test` | A member with a booking and the newsletter |
| `kenji.member@demo.ccifj.test` | A second member |
| `lucas.guest@demo.ccifj.test` | A signed-in non-member |

The database in the repository already contains them. On a fresh database, create them with
`uv run concierge-db seed-demo`. They have a published password: delete them before the agent
is opened to the public.

### If something goes wrong

| What you see | What to do |
|---|---|
| `.env is missing` | Run step 1. |
| `Set CCIFJ_AUTH_SECRET in .env` | Generate the secret in step 2 and paste it into `.env`. |
| `Port … is already in use` | Stop the other program, or choose other ports: `API_PORT=8002 WEB_PORT=5174 ./start.sh`. |
| The first answer takes a long time | The models are still loading. `http://localhost:8001/api/v1/ready` answers once they are ready. |
| "OCR is unavailable" on a scanned PDF or image | Install Tesseract with the English, French and Japanese language data. Text PDFs and Word files work without it. |
| No **Dashboard** button after signing in | Only staff accounts have it. Sign in as `staff@demo.ccifj.test`. |

---

## How to use it

### As a website visitor

1. Open the site and pick your language at the top right: EN, FR or 日本語.
2. Ask a question in your own words, for example *"Can you help my company find customers in
   Japan?"*. For advice, the concierge may first ask about your company, once; you can skip it.
3. Read the answer. The pages and documents it used are listed under it as **Sources used**,
   and suggested next questions appear under **You could also ask**.
4. Ask it to act, for example *"Help me write an email to the Business Support team."* It asks
   which language the email should use, then shows the full draft in an **Email draft** card.
5. Ask for changes until the draft is right, then tell it to send. An approval card shows
   exactly what will be stored. Choose **Store my message** and you get a reference such as
   `SR-2026-000001`. Nothing is stored unless you approve.
6. Ask about events, for example *"What events are coming up?"*. Booking a ticket, reserving
   the room and subscribing to the newsletter need a sign-in (**Sign in**, top right); your
   conversation continues after you sign in.

### As a member

Sign in as `claire.member@demo.ccifj.test`, then try:

- *"What are my bookings?"* to see your bookings and change or cancel one.
- *"Find a French food company with experience in Japanese distribution."* to get matching
  member companies with the reasons they fit, then ask for an introduction. CCIFJ staff receive
  the request and contact the other company first.
- Any email draft: it is already signed with your name, email and company.

### As CCIFJ staff

Sign in as `staff@demo.ccifj.test` and click **Dashboard** in the header. Nothing here needs
code, and the agent uses every change in its next answer.

| I want to… | Where | What to do |
|---|---|---|
| Follow up a visitor's request | **Requests** | Open **Read message** and **Read the conversation**, then set the status to New, In review or Closed. |
| See what visitors ask | **Requests → Conversations** | Open any conversation to read the questions, the answers and the sources cited. |
| Add a brochure or price list | **Knowledge files → Add files** | Choose one or more files (PDF, Word, text, image) and press **Upload and index**. |
| Keep a Calameo publication or web page up to date | **Knowledge files → Link a source** | Paste the link and press **Add link**. It is re-read every few hours; **Sync now** re-reads it immediately. |
| Add or change an event | **Events** | Edit the event, or import a spreadsheet (a template is provided). |
| Withdraw an event | **Events** | Press **Cancel event**. The agent stops listing and booking it; its bookings are kept, and **Restore** brings it back. |
| Update the member directory | **Member companies** | Import the directory export, or link a person's account to their company. |
| Take a company out of the directory | **Member companies** | Press **Hide**. The agent stops showing it in search and matching; **Show** brings it back. |
| Change who receives requests | **Staff directory** | Add or edit a contact and the topics they handle. |
| Find companies for someone's need | **Matching & intros → Find matches** | Describe the need, press **Search**, select companies, and review the drafted introduction. |
| Welcome and follow up members | **Onboarding & automation** | Run the rules for today, or for a later date to preview them, then open the outbox. |
| Give a colleague staff access | **Accounts & roles** | Change the account's role to staff. |

### On the CCIFJ website

One line in the page template adds a chat button to any page, TYPO3 included:

```html
<script src="https://your-concierge-address/embed.js" defer></script>
```

`ccifj-frontend/public/embed-demo.html` is an example page. Installation in CCIFJ's
environment is described in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

---

## What the concierge can do

| Area | What it does |
|---|---|
| **Events** | Lists upcoming events (by topic keywords, name, format, city or region such as Kansai, or dates), shows full event details, books tickets for every attendee, and reserves the L'Espace@CCIFJ room. Booking requires login and approval. |
| **My bookings** | Shows the user's own bookings and room reservations. Before booking, checks whether registration is open, whether they already booked, whether it clashes with their schedule, and whether the room is free (suggesting free slots when it isn't). Cancels a booking or reservation, or changes it (another event, add or remove attendees, new dates) in one step, until it starts. Changes need login and approval. |
| **Membership** | Explains membership categories, fees, eligibility, and benefits, shares the application form, and searches the member directory. The directory holds each company with its primary and secondary sectors and its **contact people** (name, job title, email, working language), imported from a CCIFJ directory export. Sector names are understood loosely ("F&B", "food", "agroalimentaire" all reach Food & Beverage). Today it holds the organisers' 50 fictional companies and their 116 contacts. |
| **Business support** | Knows the full CCIFJ offer across the 8 sections of ccifj.or.jp (Our Chamber, News, Membership, Events, Business Forum, Commercial Support, Recruitment/Training, Publications) and recommends the services that fit the user's stage: market studies, Business Connect, Expo+, sector missions, company setup, domiciliation, V.I.E, VAT recovery, recruitment, training, publications, and advertising. |
| **Contacts** | Reads the staff directory that CCIFJ maintains in the admin dashboard (department, what each contact handles, email, languages), picks the right contact for the request itself, falls back to the triage contact, and always offers to write the email together. A contact added in the dashboard is used on the next question; no code or topic list to update. |
| **Staff requests** | Writes an email to a CCIFJ contact, or a ticket for the CCIFJ team when no contact fits. It revises the draft with the user and stores it after the user approves. Staff read each request, and the conversation behind it, in the dashboard (**Requests → Conversations**). |
| **Member matching** | Finds the member companies that fit a business need (sector, country, experience) and shows the directory evidence for each match. A member can then ask CCIFJ for an introduction: the request is stored for staff with a reference such as `IR-2026-000012`, and staff contact the other company first. |
| **Member engagement** | Rules welcome new members, suggest a first step, relevant events and people to meet, and follow up with members who have gone quiet. Each message explains why it fits that member and appears in the member's corner of the app; staff review the outbox in the dashboard. |
| **Staff dashboard** | One place for staff to follow up and keep the agent current without coding: requests and the conversations behind them, matching and introductions, onboarding rules, events, member companies, knowledge files and links, accounts and the staff directory. See [Staff dashboard](docs/ARCHITECTURE.md#staff-dashboard). |
| **Knowledge that stays current** | Staff add brochures and procedures by upload, or link a Calameo publication, a web page or a PDF once: linked sources are re-read on a schedule and re-indexed when they change. |
| **Newsletter** | Subscribes the user to general news, training announcements, or the @StudioCCIFJ podcast, after approval. |
| **Files** | Reads attached PDFs, images (PNG, JPEG), Word documents (DOCX), and text files, with OCR in English, French, and Japanese for scans and images. Up to 4 files per conversation, 5 MB each. The text goes to the agent as context; the chat only shows the file name. |
| **Personal touch** | Asks for the details that change its advice (industry, country, B2B or B2C, stage) before answering an advisory question, once, and lets the user skip. Knows the date and the signed-in user's bookings, membership (with the company's industry and location), and newsletter status. Remembers the last few exchanges, so it builds on earlier advice instead of repeating it. |
| **Web search** | With an OpenAI model, the events and general agents can search the web for market context (competitors, market size, trade shows) and recent CCIFJ news. CCIFJ facts (fees, events, members, contacts) still come only from the tools. Switch it off with `CCIFJ_WEB_SEARCH=off`. |
| **Embedding** | `ccifj-frontend/public/embed.js` adds a chat button to any page (TYPO3 included) with one `<script>` tag; `embed-demo.html` is an example host page. |
| **Safety** | Keeps to CCIFJ topics and refuses unrelated requests. It treats tool results and documents as data, not instructions. Every database write needs explicit approval. It never shows the user its internals (databases, tools, event IDs). |

---

## Hackathon submission

42 Asia Hackathon 2026, Tokyo.

**Main agent:** Agent 1, the conversational assistant for website visitors.
**Extra agents claimed as bonuses:** Agent 3 (member matching and introductions) and
Agent 2 (member engagement). The Prisme bonus is not claimed.

| Agent | Where to look |
|---|---|
| 1. Conversational assistant | The chat on the home page, and the widget in `ccifj-frontend/public/embed-demo.html`. Staff side: **Dashboard → Requests**, with its **Conversations** tab. |
| 2. Member engagement | **Dashboard → Onboarding & automation**: the rules, a run, and the drafted follow-ups staff review before anything is sent. |
| 3. Member matching | **Dashboard → Matching & intros**, or ask the chat for a company. |
| Staff update information without coding | **Dashboard → Events, Member companies, Knowledge files, Staff directory**. |

### Evidence

- **Scenarios, fallbacks, languages:** [demo/live_eval_results.md](demo/live_eval_results.md) records a live run of
  the organisers' question bank: 19 of 20 scenarios pass and 1 is partial, 5 of 5 safe fallbacks, and all 25 answers
  cite at least one validated source. 13 questions were asked in English, 7 in French and 5 in Japanese, each
  answered in its own language. Raw answers are in `demo/*.json`.
- **Automated tests:** 269 backend tests and 48 frontend tests pass. Run them with
  `uv run python -m unittest discover -s tests -p 'test_*.py'` (backend, no API keys needed) and
  `cd ccifj-frontend && npm test`. See [Testing](#testing).
- **Sources:** answers cite the crawled ccifj.or.jp pages and the documents staff added; the chat shows them under
  each answer, and staff see them in the conversation view.
- **Installation, integration and costs:** [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

### Declarations

**Data.** Member companies, contacts and accounts are fictional: the organisers' 50 companies and demo accounts.
Knowledge content is the public ccifj.or.jp website (crawled) and the organisers' approved source documents.

**External services.**

| Service | Used for | Required |
|---|---|---|
| OpenAI API | The chat model, routing, and optional web search | Yes, unless local Ollama models are used |
| Cohere Rerank | Re-ordering search results | No; search falls back to hybrid ranking |
| Hugging Face Hub | One-time download of the `intfloat/multilingual-e5-small` embedding model, which then runs locally | Yes, on first start |
| LangSmith | Tracing during development | No; off by default |
| Odoo | Creating a CRM lead for each approved request | No; off by default |
| Calameo | Reading a linked publication's public page, or the official API when keys are set | No; only when staff link a publication |

Tesseract (OCR) and, optionally, Ollama run on the same machine as the agent.

**Reused code.** The open-source packages listed in `pyproject.toml` / `uv.lock` and
`ccifj-frontend/package.json`, mainly FastAPI, SQLModel, LangChain and LangGraph, Chroma, bm25s,
sentence-transformers, pypdf, pypdfium2, pytesseract, React and Vite.

**AI tools.** Claude Code (Anthropic) was used as a coding assistant for parts of the code, tests and
documentation.

**Simulations.** Nothing leaves the system. Requests to staff and introductions are stored for staff review; no
email is sent. Member follow-ups by email or LINE go to a mock outbox and are only marked as sent. Event bookings
are reservations, not payments.

---

## Configuration

Settings come from `.env` and are checked at startup (`src/concierge/config.py`).

| Variable | Required | Description |
|---|---|---|
| `CCIFJ_AUTH_SECRET` | yes | Secret for signing login tokens. |
| `model_type` | yes | `openai` (default) or `qwen` for local Ollama models. |
| `model_name` | yes | Main chat model. |
| `router_model_name` | yes | Model for routing and the final policy check. |
| `OPENAI_API_KEY` | with `openai` | OpenAI key. |
| `COHERE_API_KEY` | no | Cohere reranking; missing keys or reranker failures fall back to hybrid fused results. |
| `CCIFJ_STAFF_USER_IDS` | no | Comma-separated existing account IDs allowed to view the protected staff inbox. Empty disables access. |
| `trim_messages_max_token` | no | Conversation history kept per turn (default 1000 tokens). |
| `CCIFJ_AGENT_TIMEOUT_SECONDS` | no | Maximum time for one agent turn (default 180). |
| `CCIFJ_CORS_ORIGINS` | no | Comma-separated frontend origins allowed to call the API. |
| `CCIFJ_LINK_SYNC_HOURS` | no | How often linked knowledge sources (Calameo, web pages, PDFs) are re-read; default 6. `off` disables the schedule; **Sync now** in the dashboard still works. |
| `CALAMEO_API_KEY`, `CALAMEO_API_SECRET` | no | Calameo API keys (Premium/Platinum accounts). When set, linked Calameo publications are read through the official API instead of the public page, which also covers private publications. |
| `CCIFJ_WEB_SEARCH` | no | `on` (default) or `off`: OpenAI's built-in web search for the events and general agents, used for market context and recent CCIFJ news. Ignored with Ollama models. |
| `CCIFJ_WEB_SEARCH_DOMAINS` | no | Comma-separated domains the web search may use (e.g. `ccifj.or.jp`); default `*` is the whole web. |
| `ODOO_URL`, `ODOO_DB`, `ODOO_USER`, `ODOO_API_KEY` | no | When all four are set, every staff request a visitor approves is also created in Odoo CRM as a lead (`src/concierge/integrations/odoo.py`; JSON-2 API on Odoo 19+, XML-RPC on older versions; check your instance with `uv run python demo/odoo_check.py`). Off by default; an Odoo outage never fails the answer. `ODOO_TIMEOUT` (seconds, default 10). |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, … | no | LangSmith tracing. Traces go to the project `<LANGSMITH_AGENT_ID>-<LANGSMITH_AGENT_ENVIRONMENT>` (e.g. `realhackathon-local`). Runs are listed by their start time, taken from this machine's clock: keep it synced, or runs recorded while it was wrong sort above newer ones. |

Frontend settings (`ccifj-frontend/.env.local`): `CCIFJ_API_TARGET` for the
development proxy, and optionally `VITE_API_BASE_URL`. Never put API keys in
frontend variables.

---

## Testing

The contract tests use a fake agent and a throwaway database, so they need no
API keys (set any 32+ byte `CCIFJ_AUTH_SECRET` if `.env` is absent):

```bash
uv run python -m unittest discover -s tests -p 'test_*.py'
```

`scripts/verify.sh` runs the same plus the corpus check and the frontend build and
tests. Live-model checks are separate: `scripts/evaluate.py --mode api` (100
labelled questions, source hit rate) and `scripts/faq_check.py` (the organisers'
71 recurring questions in EN/FR plus five fallback cases: missing price,
conflicting price, unknown member, unavailable event, unsupported policy). Both
need a running, warmed API and call the model.

Frontend tests:

```bash
cd ccifj-frontend && npm test
```

---

## More documentation

| Document | What it covers |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How a message is answered, the agents and tools, approvals, search, the staff dashboard, the API, the database and the project structure |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Installing it in CCIFJ's environment (TYPO3, Odoo, GetResponse), keeping content current, running costs and who maintains what |
| [docs/HACKATHON.md](docs/HACKATHON.md) | The demo runbook: staff access, the walkthrough and the evaluation commands |
| [demo/live_eval_results.md](demo/live_eval_results.md) | The live evaluation on the organisers' question bank |
| [src/concierge/api/README.md](src/concierge/api/README.md) | The full API reference |
| [src/concierge/db/README.md](src/concierge/db/README.md) | The database tables |
| [ccifj-frontend/README.md](ccifj-frontend/README.md) | The website and dashboard code |
