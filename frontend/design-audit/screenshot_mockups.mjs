// T17, шаг 2: скриншоты статических макетов (docs/design/mockups/*.html) —
// без бэкенда, просто рендерим локальные файлы и снимаем PNG рядом.
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const mockupsDir = path.resolve(__dirname, "../../docs/design/mockups");
const port = 4173;

const server = spawn("python3", ["-m", "http.server", String(port)], { cwd: mockupsDir, stdio: "ignore" });
await new Promise((r) => setTimeout(r, 800));

const pages = [
  "index.html",
  "application-approved.html",
  "application-hardstop.html",
  "application-grayzone-fssp.html",
  "applications-list.html",
  "scoring.html",
  "analytics.html",
  "future-scoring-card.html",
];

try {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  const page = await context.newPage();
  for (const file of pages) {
    await page.goto(`http://localhost:${port}/${file}`, { waitUntil: "networkidle" });
    await page.waitForTimeout(150);
    const outPath = path.resolve(mockupsDir, file.replace(".html", ".png"));
    await page.screenshot({ path: outPath, fullPage: true });
    console.log(`ok: ${file} -> ${path.basename(outPath)}`);
  }
  await browser.close();
} finally {
  server.kill();
}
