// T20 — скриншоты после финальной полировки: карточка (одобрено / серая зона /
// отказ) и «Состояние системы». Учётки — из последнего сида Playwright
// (e2e/.seed.json), заявки — реальные, прошедшие конвейер в dev-стеке.
// usage: node design-audit/screenshot_t20.mjs <approved-id> <gray-id> <reject-id> [<extra-slug>=<id> ...]
import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "../..");
const baseUrl = "http://localhost:5173";
const seed = JSON.parse(readFileSync(path.resolve(__dirname, "../e2e/.seed.json"), "utf-8"));
// T21: папку можно переопределить (SHOTS_DIR=t21-pti), чтобы не затирать T20.
const outDir = path.resolve(repoRoot, "docs/design/after", process.env.SHOTS_DIR ?? "t20-polish");
mkdirSync(outDir, { recursive: true });

const [approved, gray, reject, ...extra] = process.argv.slice(2);
if (!approved || !gray || !reject) {
  console.error("usage: node screenshot_t20.mjs <approved-id> <gray-id> <reject-id>");
  process.exit(1);
}

const shots = [
  ["risk_manager", `/applications/${approved}`, "card-approved", false],
  ["risk_manager", `/applications/${gray}`, "card-gray-zone", false],
  ["risk_manager", `/applications/${gray}`, "card-gray-zone-help-open", true],
  ["risk_manager", `/applications/${reject}`, "card-rejected", false],
  ["admin", "/system", "system-status", false],
  ...extra.map((pair) => {
    const [slug, id] = pair.split("=");
    return ["risk_manager", `/applications/${id}`, slug, false];
  }),
  // Остальные экраны — те же токены и секции (обзор, не по одному на роль).
  ...(process.env.SKIP_OVERVIEW ? [] : [
  ["risk_manager", "/applications", "overview/applications-list", false],
  ["risk_manager", "/dashboard", "overview/analytics", false],
  ["risk_manager", "/rulesets", "overview/scoring", false],
  ["risk_manager", "/help", "overview/help", false],
  ["risk_manager", "/imports", "overview/imports", false],
  ["admin", "/employees", "overview/employees", false],
  ["admin", "/references", "overview/references", false],
  ]),
];
mkdirSync(path.resolve(outDir, "overview"), { recursive: true });

const browser = await chromium.launch();
for (const role of ["risk_manager", "admin"]) {
  const context = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  const login = await context.request.post(`${baseUrl}/api/auth/login`, {
    data: { email: seed[role].email, password: seed[role].password },
    headers: { "X-Requested-With": "XMLHttpRequest" },
  });
  if (!login.ok()) throw new Error(`login failed for ${role}: ${login.status()}`);
  const page = await context.newPage();
  for (const [shotRole, route, slug, openHelp] of shots) {
    if (shotRole !== role) continue;
    await page.goto(`${baseUrl}${route}`, { waitUntil: "networkidle" });
    if (openHelp) await page.locator(".help-toggle").click();
    await page.waitForTimeout(200);
    await page.screenshot({ path: path.resolve(outDir, `${slug}.png`), fullPage: true });
    console.log(`ok: ${slug}`);
  }
  await context.close();
}
await browser.close();
