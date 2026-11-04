# CCIFJ frontend

Standalone React + TypeScript chat site in the CCIFJ blue, magenta and white theme. It contains the interface, translations, API client and request/response types, and talks to the `concierge` API in this repository (`../src/concierge`).

## Start

Start the API first (from the repository root, see `../src/concierge/api/README.md`):

```bash
uv run uvicorn concierge.main:app --app-dir src --reload
```

Then, in a second terminal:

```bash
cd ccifj-frontend
npm ci
npm run dev
```

Open **http://127.0.0.1:5173/**. The Vite proxy forwards `/api` to http://127.0.0.1:8000. There are no simulated answers.

The welcome screen's fallback starter questions read the shared catalogs from `../src/concierge/data/` (`predefined_qa.json`, `predefined_qa_i18n.json`), so run Vite from this folder inside the repository. Questions under an answer are written by the agent; there are no pre-written answers.

## Configure the API

```bash
cp .env.example .env.local
```

Edit `CCIFJ_API_TARGET` for the development/preview proxy. Restart Vite after changing it. `VITE_API_BASE_URL` is optional for direct requests or deployment; configure the backend to allow the frontend origin through `CCIFJ_CORS_ORIGINS`. Never place API keys in frontend environment variables.

- **`src/endpoints.ts`** — route constants and backend base URL.
- **`src/api.ts`** — HTTP/SSE client, response normalization, timeouts and errors.
- **`src/types.ts`** — TypeScript request/response contracts.
- **`API.md`** — the endpoints used by this frontend.

## Languages

The interface follows the browser language (English, French or Japanese); `?lang=en`, `?lang=fr` or `?lang=ja` overrides it. Each message is sent with the locale detected from its text, falling back to the interface language.

Site translations: `src/site-copy.ts`. Chat translations: `src/i18n.ts` and `src/WidgetChat.tsx`.

## Build and preview

```bash
npm test
npm run build
npm run preview
```

Preview: http://127.0.0.1:4173/. Deploy `dist/` to a static host. That host must proxy `/api` to your backend, or set `VITE_API_BASE_URL` before building and allow the frontend origin in backend CORS.
