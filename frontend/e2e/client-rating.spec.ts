// T23: рейтинг клиента (скоркарта заказчика) в карточке заявки — сумма и
// плитки критериев.
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("карточка: блок «Рейтинг клиента» с суммой и критериями", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto(`/applications/${seed.application.id}`);

  const rating = page.getByTestId("client-rating");
  await expect(rating.getByRole("heading", { name: "Рейтинг клиента" })).toBeVisible();
  // Плитка на каждый критерий: название и баллы.
  await expect(rating.locator(".cr-item")).toHaveCount(4);
  for (const criterion of ["Возраст", "Контакты", "Работа", "Прописка"]) {
    await expect(rating.locator(".cr-item__title", { hasText: criterion })).toBeVisible();
  }
  await expect(rating.getByText("баллов", { exact: true })).toBeVisible();
});
