import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const proxyTimeoutMs = Number(env.CCIFJ_PROXY_TIMEOUT_MS || 600000);
  const proxy = { '/api': { target: env.CCIFJ_API_TARGET || 'http://127.0.0.1:8000', changeOrigin: true, timeout: proxyTimeoutMs, proxyTimeout: proxyTimeoutMs } };
  return {
    plugins: [react()],
    server: { host: '127.0.0.1', port: 5173, strictPort: true, proxy, fs: { allow: [process.cwd(), resolve(process.cwd(), '..', 'src', 'concierge', 'data')] } },
    preview: { host: '127.0.0.1', port: 4173, strictPort: true, proxy },
  };
});
