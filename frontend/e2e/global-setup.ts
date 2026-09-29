import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Досеивает сотрудников и одну заявку с расхождением перед прогоном (см.
// seed.py — прямые вставки в БД, без гонки реального конвейера). Копирует
// скрипт в контейнер worker (как scripts/e2e_check.py делает для
// _e2e_runner.py) и запускает его там — код приложения и доступ к БД есть
// только внутри контейнера.
export default function globalSetup(): void {
  const repoRoot = path.resolve(__dirname, "../..");
  const seedPath = path.resolve(__dirname, "seed.py");

  execFileSync("docker", ["compose", "cp", seedPath, "worker:/app/_e2e_seed.py"], { cwd: repoRoot });
  const output = execFileSync(
    "docker",
    ["compose", "exec", "-T", "worker", "python3", "/app/_e2e_seed.py"],
    { cwd: repoRoot },
  );

  const seed = JSON.parse(output.toString("utf-8").trim().split("\n").pop()!);
  writeFileSync(path.resolve(__dirname, ".seed.json"), JSON.stringify(seed, null, 2), "utf-8");
}
