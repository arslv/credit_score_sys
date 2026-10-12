// T23: «Скоринг» после симуляции (версия правил не публикуется — только
// «Проверить на истории»). usage: DEMO_SEED_PASSWORD=... node design-audit/screenshot_t23_sim.mjs [папка]
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const baseUrl = process.env.BASE_URL ?? "http://localhost:5173";
const outDir = path.resolve(path.resolve(__dirname, "../.."), "docs/design/after", process.argv[2] ?? "t23");
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const login = await context.request.post(`${baseUrl}/api/auth/login`, {
  data: { email: "demo.risk@example.com", password: process.env.DEMO_SEED_PASSWORD },
  headers: { "X-Requested-With": "XMLHttpRequest" },
});
if (!login.ok()) throw new Error(`вход: HTTP ${login.status()}`);
const page = await context.newPage();
await page.goto(`${baseUrl}/rulesets`, { waitUntil: "networkidle" });
await page.getByLabel("Автоодобрение выше").fill("740");
await page.getByRole("button", { name: "Проверить на истории" }).click();
await page.getByText(/В выборке \d+/).waitFor({ timeout: 20_000 });
await page.screenshot({ path: path.resolve(outDir, "risk_manager", "rulesets-simulation.png"), fullPage: true });
await browser.close();
console.log("ok: risk_manager/rulesets-simulation");
