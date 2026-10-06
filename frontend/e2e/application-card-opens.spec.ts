import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("открытие карточки заявки", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.risk_manager.email, seed.risk_manager.password);

  await page.goto(`/applications/${seed.application.id}`);
  // T17, шаг 3: вместо заголовка «Решения» — вердикт крупно, номер заявки в шапке вердикта.
  await expect(page.getByText(/Заявка №\d+/).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Что проверили" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "История" })).toBeVisible();
  await expect(page.getByText(seed.application.last_name).first()).toBeVisible();
});
