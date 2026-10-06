// T17, шаг 3 — скриншоты «после» для каждой части редизайна. Переиспользует
// employees/заявки, посеянные в design-audit/seed.py на шаге 1 (аудит) —
// не пересеивает каждый раз, чтобы не копить дубликаты в dev-БД.
import { mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "../..");
const baseUrl = "http://localhost:5173";

const PASSWORD = "design-audit-password-123";
const RUN = "fa0f65fe"; // суффикс из шага 1 (docs/design/before) — те же employees/заявки
const employees = {
  risk_manager: `design-risk_manager-${RUN}@example.com`,
  admin: `design-admin-${RUN}@example.com`,
  viewer: `design-viewer-${RUN}@example.com`,
  compliance: `design-compliance-${RUN}@example.com`,
  operator: `design-operator-${RUN}@example.com`,
};
const full = "891ed305-6647-4768-8df0-1964f580ccf7";
const frozen = "94f21eea-6566-4358-bcc0-5ea918a1dc41";
const disc = "c49796d1-9e98-44fd-aecb-3156ea57b9e1";

// Передаётся аргументом: имя подпапки в docs/design/after/ и список
// [role, [[route, slug], ...]] — что именно снимать для этой части.
const partName = process.argv[2];
if (!partName) {
  console.error("usage: node screenshot_after.mjs <part-name>");
  process.exit(1);
}
const outDir = path.resolve(repoRoot, "docs/design/after", partName);

const PAGES_BY_ROLE = {
  risk_manager: [["/dashboard", "analytics"], ["/contracts", "portfolio-subnav"], [`/applications/${full}`, "application-full"]],
  admin: [["/employees", "settings-subnav"], ["/applications", "applications-list"]],
  viewer: [["/applications", "applications-list"]],
  compliance: [["/compliance", "settings-compliance"], [`/applications/${frozen}`, "application-frozen"]],
  operator: [["/applications", "applications-list"], [`/applications/${full}`, "application-full"]],
};

async function main() {
  const browser = await chromium.launch();
  for (const [role, pages] of Object.entries(PAGES_BY_ROLE)) {
    const context = await browser.newContext({ viewport: { width: 1366, height: 900 } });
    const loginResp = await context.request.post(`${baseUrl}/api/auth/login`, {
      data: { email: employees[role], password: PASSWORD },
      headers: { "X-Requested-With": "XMLHttpRequest" },
    });
    if (!loginResp.ok()) {
      console.error(`login failed for ${role}: ${loginResp.status()}`);
      await context.close();
      continue;
    }
    const roleDir = path.resolve(outDir, role);
    mkdirSync(roleDir, { recursive: true });
    const page = await context.newPage();
    for (const [route, slug] of pages) {
      try {
        await page.goto(`${baseUrl}${route}`, { waitUntil: "networkidle", timeout: 15000 });
        await page.waitForTimeout(250);
        await page.screenshot({ path: path.resolve(roleDir, `${slug}.png`), fullPage: true });
        console.log(`ok: ${role} ${route} -> ${slug}.png`);
      } catch (err) {
        console.error(`FAILED: ${role} ${route}: ${err.message}`);
      }
    }
    await context.close();
  }
  await browser.close();
}

await main();
