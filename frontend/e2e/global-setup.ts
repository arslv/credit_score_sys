import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Досеивает сотрудников и заявки перед прогоном (см. seed.py — прямые вставки в
// БД, без гонки реального конвейера). Скрипт подаётся в контейнер worker через
// stdin, без копирования внутрь контейнера: код приложения и доступ к БД есть
// только там, а копия в контейнере переживает правки и даёт прогон на старом коде.
export default function globalSetup(): void {
  const repoRoot = path.resolve(__dirname, "../..");
  const seedPath = path.resolve(__dirname, "seed.py");

  const output = execFileSync(
    "docker",
    ["compose", "exec", "-T", "worker", "python3", "-"],
    { cwd: repoRoot, input: readFileSync(seedPath) },
  );

  const seed = JSON.parse(output.toString("utf-8").trim().split("\n").pop()!);
  writeFileSync(path.resolve(__dirname, ".seed.json"), JSON.stringify(seed, null, 2), "utf-8");
}
