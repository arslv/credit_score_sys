import { defineConfig } from "@playwright/test";

// Требует поднятого docker compose (frontend+api+db+redis+fake_partner+worker+beat) —
// это сквозные тесты через реальный HTTP, не моки (тот же принцип, что и
// scripts/e2e_check.py для бэкенда). Перед прогоном globalSetup досеивает
// сотрудников и одну заявку с расхождением через backend/scripts/e2e_seed.py.
export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  // T22: сверяет, что активная версия правил в dev-базе не изменилась.
  globalTeardown: "./e2e/global-teardown.ts",
  reporter: [["list"]],
  workers: 1, // сиды общие для всех тестов — параллельность дала бы гонки
  projects: [
    {
      // Обычные тесты — на dev-стеке с накопленными данными (аналитика,
      // списки и т.п. на пустой базе проверять нечего).
      name: "dev",
      testIgnore: /\.isolated\.spec\.ts$/,
      use: { baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173" },
    },
    {
      // T22: тесты, которые меняют append-only данные с глобальным эффектом
      // (публикация/откат версий правил), — на отдельной базе dialog_e2e
      // (api_e2e/frontend_e2e, docker compose --profile e2e).
      name: "isolated-db",
      testMatch: /\.isolated\.spec\.ts$/,
      use: { baseURL: process.env.E2E_ISOLATED_BASE_URL ?? "http://localhost:5174" },
    },
  ],
});
