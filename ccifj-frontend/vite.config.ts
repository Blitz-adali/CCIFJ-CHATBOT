import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const proxyTimeoutMs = Number(env.CCIFJ_PROXY_TIMEOUT_MS || 600000);
  const proxy = { '/api': { target: env.CCIFJ_API_TARGET || 'http://127.0.0.1:8000', changeOrigin: false, ws: true, timeout: proxyTimeoutMs, proxyTimeout: proxyTimeoutMs } };
  return {
    plugins: [react()],
    server: { host: '127.0.0.1', port: 5173, strictPort: true, proxy },
    preview: { host: '127.0.0.1', port: 4173, strictPort: true, proxy },
  };
});
