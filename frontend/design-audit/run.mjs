// T17, шаг 1: разовый скрипт аудита — не часть обычного набора Playwright
// (frontend/e2e/*.spec.ts), не запускается через `npm run test:e2e`.
// Запуск: node design-audit/run.mjs (из frontend/, при поднятом docker compose).
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "../..");
const baseUrl = "http://localhost:5173";
const outDir = path.resolve(repoRoot, "docs/design/before");

execFileSync("docker", ["compose", "cp", path.resolve(__dirname, "seed.py"), "worker:/app/_design_audit_seed.py"], {
  cwd: repoRoot,
});
const seedOutput = execFileSync(
  "docker", ["compose", "exec", "-T", "worker", "python3", "/app/_design_audit_seed.py"],
  { cwd: repoRoot },
).toString("utf-8");
const seed = JSON.parse(seedOutput.trim().split("\n").pop());
console.log("seed:", JSON.stringify(seed, null, 2));

const disc = seed.discrepancy.id;
const full = seed.full.id;
const frozen = seed.frozen.id;

// path, filename-slug для каждой роли — ровно то, что видно в навигации
// (Layout.tsx NAV_ITEMS) плюс карточки заявок, где роли расходятся по ПД.
const PAGES_BY_ROLE = {
  risk_manager: [
    ["/dashboard", "dashboard"],
    ["/applications", "applications-list"],
    [`/applications/${disc}`, "application-detail-discrepancy"],
    [`/applications/${full}`, "application-detail-full-approved"],
    ["/clients", "clients-list"],
    ["/contracts", "contracts-list"],
    ["/rulesets", "rulesets"],
    ["/imports", "imports"],
    ["/references", "references"],
  ],
  admin: [
    ["/applications", "applications-list"],
    [`/applications/${full}`, "application-detail-full-masked"],
    ["/clients", "clients-list"],
    ["/contracts", "contracts-list"],
    ["/imports", "imports"],
    ["/references", "references"],
    ["/employees", "employees"],
    ["/audit", "audit"],
    ["/system", "system"],
  ],
  viewer: [
    ["/dashboard", "dashboard"],
    ["/applications", "applications-list"],
    [`/applications/${full}`, "application-detail-full-masked"],
    ["/clients", "clients-list"],
    ["/contracts", "contracts-list"],
  ],
  compliance: [
    ["/applications", "applications-list"],
    [`/applications/${frozen}`, "application-detail-frozen-sanctions"],
    [`/applications/${full}`, "application-detail-full"],
    ["/clients", "clients-list"],
    ["/contracts", "contracts-list"],
    ["/compliance", "compliance"],
  ],
  operator: [
    ["/applications", "applications-list"],
    [`/applications/${full}`, "application-detail-full-with-fssp"],
    [`/applications/${disc}`, "application-detail-discrepancy"],
  ],
};

async function main() {
  const browser = await chromium.launch();
  for (const [role, pages] of Object.entries(PAGES_BY_ROLE)) {
    const { email, password } = seed.employees[role];
    const context = await browser.newContext({ viewport: { width: 1366, height: 900 } });
    const loginResp = await context.request.post(`${baseUrl}/api/auth/login`, {
      data: { email, password },
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
        await page.waitForTimeout(300);
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
