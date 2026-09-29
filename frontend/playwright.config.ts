import { defineConfig } from "@playwright/test";

// Требует поднятого docker compose (frontend+api+db+redis+fake_partner+worker+beat) —
// это сквозные тесты через реальный HTTP, не моки (тот же принцип, что и
// scripts/e2e_check.py для бэкенда). Перед прогоном globalSetup досеивает
// сотрудников и одну заявку с расхождением через backend/scripts/e2e_seed.py.
export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
  },
  reporter: [["list"]],
  workers: 1, // сиды общие для всех тестов — параллельность дала бы гонки
});
