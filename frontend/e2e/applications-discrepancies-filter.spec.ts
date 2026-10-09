import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("список заявок — фильтр «расхождения с оператором»", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.risk_manager.email, seed.risk_manager.password);
  await page.goto("/applications");

  // T17, часть 4: у риск-менеджера по умолчанию открыта его очередь «Требуют
  // действия» (сортировка — дольше всех ждущие сверху, растёт с каждым
  // прогоном e2e). Фильтр «расхождения» проверяем на «Все» — там сортировка
  // по дате подачи (новые сверху), и свежая сидовая заявка — первая.
  await page.getByRole("button", { name: /Все · \d+/ }).click();
  // Старые фильтры спрятаны под «Ещё фильтры» (по умолчанию свёрнуто).
  await page.getByText("Ещё фильтры").click();
  await page.getByLabel("только расхождения с оператором").check();
  await expect(page.getByText(seed.application.external_id)).toBeVisible();
  await expect(page.getByText("Оператор изменил статус").first()).toBeVisible();
});
