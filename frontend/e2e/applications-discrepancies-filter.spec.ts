import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("список заявок — фильтр «расхождения с оператором»", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.risk_manager.email, seed.risk_manager.password);
  await page.goto("/applications");

  // T17, часть 4: старые фильтры спрятаны под «Ещё фильтры» (по умолчанию свёрнуто).
  await page.getByText("Ещё фильтры").click();
  await page.getByLabel("только расхождения с оператором").check();
  await expect(page.getByText(seed.application.external_id)).toBeVisible();
  await expect(page.getByText("Оператор изменил статус").first()).toBeVisible();
});
