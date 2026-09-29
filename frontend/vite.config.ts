import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Один origin для фронтенда и API (T13, ваше уточнение): браузер обращается
// только к этому серверу, Vite сам проксирует /api на бэкенд — межсайтовых
// запросов нет, кука работает без какой-либо настройки CORS. В бою тот же
// принцип обеспечивает nginx (см. docs/deployment.md).
const apiProxyTarget = process.env.VITE_API_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: apiProxyTarget,
        changeOrigin: true,
      },
    },
  },
});
