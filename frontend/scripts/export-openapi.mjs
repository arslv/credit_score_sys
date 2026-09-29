// Скачивает openapi.json с работающего бэкенда — типы генерируются из
// реальной схемы FastAPI, а не пишутся руками (ваше уточнение): если бэкенд
// поменяет форму ответа, следующий `npm run gen:api` + tsc --noEmit это
// обнаружит на этапе сборки, а не в рантайме у пользователя.
import { writeFileSync } from "node:fs";

const target = process.env.VITE_API_PROXY_TARGET ?? "http://localhost:8000";
const url = `${target}/openapi.json`;

const response = await fetch(url);
if (!response.ok) {
  console.error(`не удалось получить ${url}: ${response.status} ${response.statusText}`);
  console.error("бэкенд должен быть поднят (docker compose up -d api) перед npm run gen:api");
  process.exit(1);
}

const schema = await response.json();
writeFileSync("./openapi.json", JSON.stringify(schema, null, 2) + "\n", "utf-8");
console.log(`openapi.json сохранён (${url})`);
