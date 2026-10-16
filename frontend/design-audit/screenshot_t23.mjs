// T23 — скриншоты ВСЕХ экранов каждой роли на демо-данных (app.cli.seed_demo).
// Пароль демо-аккаунтов — из окружения (DEMO_SEED_PASSWORD, тот же, что
// передавали seed_demo --password), не из кода.
// usage: DEMO_SEED_PASSWORD=... node design-audit/screenshot_t23.mjs [папка-в-docs/design/after]
import { mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "../..");
const baseUrl = process.env.BASE_URL ?? "http://localhost:5173";
const password = process.env.DEMO_SEED_PASSWORD;
if (!password) {
  console.error("нужен DEMO_SEED_PASSWORD — пароль, переданный seed_demo --password");
  process.exit(1);
}
// Папка: аргумент — путь относительно docs/design/after или абсолютный.
const outDir = path.resolve(repoRoot, "docs/design/after", process.argv[2] ?? "t23");
// ONLY=admin,operator — снять не все роли (для быстрой проверки).
const only = process.env.ONLY ? new Set(process.env.ONLY.split(",")) : null;

// T23: ролей три — admin, operator, viewer.
const ROLES = {
  operator: ["/applications", "/compliance", "/contracts", "/clients", "/help"],
  viewer: ["/applications", "/dashboard", "/contracts", "/clients", "/help"],
  admin: [
    "/applications", "/compliance", "/rulesets", "/dashboard", "/contracts", "/clients", "/imports", "/employees",
    "/references", "/audit", "/system", "/help",
  ],
};
const EMAIL = (role) => `demo.${role}@example.com`;
const CARDS = ["approved", "gray_zone", "rejected_a", "rejected_b", "to_operator"];

async function firstId(request, url) {
  const response = await request.get(`${baseUrl}${url}`);
  if (!response.ok()) return null;
  const body = await response.json();
  return body.items?.[0]?.id ?? null;
}

const browser = await chromium.launch();
{
  // Вход — без авторизации.
  mkdirSync(outDir, { recursive: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.goto(`${baseUrl}/login`, { waitUntil: "networkidle" });
  await page.screenshot({ path: path.resolve(outDir, "login.png") });
  await context.close();
}
for (const [role, routes] of Object.entries(ROLES)) {
  if (only && !only.has(role)) continue;
  mkdirSync(path.resolve(outDir, role), { recursive: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const login = await context.request.post(`${baseUrl}/api/auth/login`, {
    data: { email: EMAIL(role), password },
    headers: { "X-Requested-With": "XMLHttpRequest" },
  });
  if (!login.ok()) throw new Error(`вход ${role}: HTTP ${login.status()}`);

  const shots = routes.map((route) => [route, route.slice(1)]);
  for (const status of CARDS) {
    const id = await firstId(context.request, `/api/applications?status=${status}&page_size=1`);
    if (id) shots.push([`/applications/${id}`, `card-${status}`]);
  }
  if (routes.includes("/contracts")) {
    const id = await firstId(context.request, "/api/contracts?page_size=1");
    if (id) shots.push([`/contracts/${id}`, "contract-detail"]);
  }
  if (routes.includes("/clients")) {
    const id = await firstId(context.request, "/api/clients?page_size=1");
    if (id) shots.push([`/clients/${id}`, "client-detail"]);
  }

  const page = await context.newPage();
  for (const [route, slug] of shots) {
    await page.goto(`${baseUrl}${route}`, { waitUntil: "networkidle" });
    // В dev-базе могут быть настоящие учётки (не демо) — в репозиторий их
    // ФИО и email не попадают: «Сотрудники» — с поиском по демо-аккаунтам,
    // «Аудит» — с фильтром по демо-оператору (заодно видны сами фильтры).
    if (route === "/employees") {
      await page.getByRole("searchbox", { name: "Имя или email" }).fill("(демо)");
    }
    if (route === "/audit") {
      await page.getByRole("combobox", { name: "Сотрудник" }).selectOption({ label: "Оператор (демо)" });
      await page.waitForLoadState("networkidle");
    }
    await page.waitForTimeout(250);
    await page.screenshot({ path: path.resolve(outDir, role, `${slug}.png`), fullPage: true });
    console.log(`ok: ${role}/${slug}`);
  }
  await context.close();
}
await browser.close();
