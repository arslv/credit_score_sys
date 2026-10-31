import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const repoRoot = path.resolve(__dirname, "../..");
export const E2E_ISOLATED_BASE_URL = process.env.E2E_ISOLATED_BASE_URL ?? "http://localhost:5174";

// Скрипт подаётся в контейнер через stdin, без копирования внутрь контейнера:
// копия переживает правки и даёт прогон на старом коде (T17).
export function runInContainer(service: string, script: string, env: Record<string, string> = {}): string {
  const envArgs = Object.entries(env).flatMap(([k, v]) => ["-e", `${k}=${v}`]);
  const output = execFileSync(
    "docker",
    ["compose", "--profile", "e2e", "exec", "-T", ...envArgs, service, "python3", "-"],
    { cwd: repoRoot, input: readFileSync(path.resolve(__dirname, script)) },
  );
  return output.toString("utf-8").trim().split("\n").pop()!;
}

async function waitForIsolatedStack(timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastError = "";
  while (Date.now() < deadline) {
    try {
      // 401/403 — значит и фронт, и прокси на api_e2e, и сам api_e2e живы.
      const response = await fetch(`${E2E_ISOLATED_BASE_URL}/api/rulesets/active`);
      if (response.status === 401 || response.status === 403) return;
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(`отдельный e2e-стек (api_e2e/frontend_e2e) не поднялся: ${lastError}`);
}

// Досеивает сотрудников и заявки перед прогоном (см. seed.py — прямые вставки в
// БД, без гонки реального конвейера) — в dev-базу (worker) и, теми же учётками,
// в отдельную dialog_e2e (api_e2e), где идут тесты публикации/отката правил.
export default async function globalSetup(): Promise<void> {
  // T22: отдельный стек — пересобирается каждый раз (--build): образ api_e2e
  // собирается из backend/, иначе тесты шли бы на старом коде.
  execFileSync(
    "docker", ["compose", "--profile", "e2e", "up", "-d", "--build", "api_e2e", "frontend_e2e"],
    { cwd: repoRoot, stdio: "inherit" },
  );
  await waitForIsolatedStack(180_000);

  writeFileSync(
    path.resolve(__dirname, ".active-ruleset.json"), runInContainer("api", "active_ruleset.py"), "utf-8",
  );

  // Сид — только в отдельную базу dialog_e2e: dev-база — витрина для показа,
  // тестовые заявки и сотрудники в неё не попадают.
  const run = randomBytes(4).toString("hex");
  const seed = JSON.parse(runInContainer("api_e2e", "seed.py", { SEED_RUN: run }));
  writeFileSync(path.resolve(__dirname, ".seed.json"), JSON.stringify(seed, null, 2), "utf-8");
  await acceptRulesetFromFile(seed.admin.email, seed.admin.password);
}

// Активные правила отдельной базы — из файла config/rulesets/dev.yaml: тест
// публикации и отката (*.isolated.spec.ts) копирует действующую версию, и без
// этого шага там годами тянулась бы старая (например, без client_rating).
// Совпадает с файлом — ничего не создаётся.
async function acceptRulesetFromFile(email: string, password: string): Promise<void> {
  const headers = { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" };
  const login = await fetch(`${E2E_ISOLATED_BASE_URL}/api/auth/login`, {
    method: "POST", headers, body: JSON.stringify({ email, password }),
  });
  if (!login.ok) throw new Error(`вход админа e2e: HTTP ${login.status}`);
  const cookie = (login.headers.get("set-cookie") ?? "").split(";")[0];
  const accept = await fetch(`${E2E_ISOLATED_BASE_URL}/api/rulesets/accept-file`, {
    method: "POST", headers: { ...headers, cookie },
  });
  if (!accept.ok) throw new Error(`правила из файла в dialog_e2e: HTTP ${accept.status} ${await accept.text()}`);
}
