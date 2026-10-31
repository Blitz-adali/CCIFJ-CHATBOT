# CCIFJ frontend

Standalone React + TypeScript frontend in the CCIFJ blue, magenta and white theme. Contains the interface, translations, API client and request/response types. The FastAPI backend is in `../src/concierge/`; no dashboard, crawler or browser extension is included.

## Start

```bash
cd ccifj-frontend
npm install
npm run dev
```

Open **http://127.0.0.1:5173/**. Use `npm ci` with the supplied lockfile for a reproducible installation.

The frontend requires the local API to send messages. Follow `../src/concierge/api/README.md` to start it on http://127.0.0.1:8000. The default Vite proxy points there. There are no simulated answers.

## Configure the API

```bash
cp .env.example .env.local
```

Edit `CCIFJ_API_TARGET` for the development/preview proxy. Restart Vite after changing it. `VITE_API_BASE_URL` is optional for direct requests or deployment; configure the backend to allow the frontend origin through CORS. Never place API keys in frontend environment variables.

- **`src/endpoints.ts`** — route constants and backend base URL.
- **`src/api.ts`** — HTTP client, timeouts and errors.
- **`src/types.ts`** — TypeScript request/response contracts.
- **`API.md`** — complete documentation for the endpoints used by this frontend.

## Languages

One selector controls the entire interface and the locale in chat requests: English, French and Japanese. It remembers the choice locally. `?lang=en`, `?lang=fr` or `?lang=ja` overrides the stored choice. Switching languages keeps the conversation; existing replies stay in their original language. English uses “Hello”; French uses “Bonjour”.

Site translations: `src/site-copy.ts`. Chat translations: `src/i18n.ts` and `src/WidgetChat.tsx`.

## Build and preview

```bash
npm test
npm run build
npm run preview
```

Preview: http://127.0.0.1:4173/. Deploy `dist/` to a static host. That host must proxy `/api` to your backend, or set `VITE_API_BASE_URL` before building and allow the frontend origin in backend CORS.
