import createClient from "openapi-fetch";
import type { paths } from "./schema";

// Один origin (T13, ваше уточнение) — относительный /api, Vite (dev) или
// nginx (бой) сами проксируют на бэкенд. Кука идёт по умолчанию (same-origin
// запрос её всегда несёт) — credentials: "include" не нужен вовсе.
export const api = createClient<paths>({ baseUrl: "" });

const CSRF_HEADER = "X-Requested-With";
const CSRF_VALUE = "XMLHttpRequest";

// Единый колбэк на 401 — очищает кэш запросов и уводит на /login (ваше
// уточнение про персданные в браузере: при 401 кэш обязан быть пуст).
type Unauthorized = () => void;
let onUnauthorized: Unauthorized | null = null;
export function setUnauthorizedHandler(handler: Unauthorized): void {
  onUnauthorized = handler;
}

api.use({
  onRequest({ request }) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      request.headers.set(CSRF_HEADER, CSRF_VALUE);
    }
    return request;
  },
  onResponse({ response }) {
    if (response.status === 401 && onUnauthorized) {
      onUnauthorized();
    }
    return response;
  },
});
