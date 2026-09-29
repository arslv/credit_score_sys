import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Не через globalSetup: сиды создают сотрудника с известным паролем прямо в
// базе, в обход входа/аудита — в APP_ENV=production скрипт обязан отказаться
// запускаться, тем же способом, что app/startup_guard.py для фейковых
// провайдеров (ваше уточнение).
test("сид-скрипт отказывается запускаться при APP_ENV=production", () => {
  const repoRoot = path.resolve(__dirname, "../..");
  const seedPath = path.resolve(__dirname, "seed.py");
  execFileSync("docker", ["compose", "cp", seedPath, "worker:/app/_e2e_seed_guard_check.py"], {
    cwd: repoRoot,
  });

  let failed = false;
  let stderr = "";
  try {
    execFileSync(
      "docker",
      ["compose", "exec", "-T", "-e", "APP_ENV=production", "worker", "python3", "/app/_e2e_seed_guard_check.py"],
      { cwd: repoRoot, stdio: ["ignore", "pipe", "pipe"] },
    );
  } catch (error) {
    failed = true;
    stderr = String((error as { stderr?: Buffer }).stderr ?? "");
  }

  expect(failed).toBe(true);
  expect(stderr).toContain("APP_ENV=production");
});
