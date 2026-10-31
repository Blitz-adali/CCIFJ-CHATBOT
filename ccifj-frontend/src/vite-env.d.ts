/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_CHAT_TRANSPORT?: "websocket" | "sse" | "json";
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_API_TIMEOUT_MS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
