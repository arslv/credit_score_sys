import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("открытие карточки заявки", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.risk_manager.email, seed.risk_manager.password);

  await page.goto(`/applications/${seed.application.id}`);
  await expect(page.getByRole("heading", { name: /Заявка №/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Решения" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Проверки" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "История" })).toBeVisible();
  await expect(page.getByText(seed.application.last_name).first()).toBeVisible();
});
