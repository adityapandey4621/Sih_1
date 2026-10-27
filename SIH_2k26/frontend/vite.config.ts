import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// The SPA and API share an origin in every environment: Vite proxies /api in development,
// a reverse proxy does the same in production. Cookies therefore never cross origins.
const API_TARGET = process.env.VITE_API_PROXY_TARGET ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: API_TARGET, changeOrigin: false } },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
    css: false,
    testTimeout: 15000, // a slow CI or a busy laptop must not turn a correct test red
  },
})
