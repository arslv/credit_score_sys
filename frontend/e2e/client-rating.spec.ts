// T23: рейтинг клиента (скоркарта заказчика) в карточке заявки — сумма,
// плитки критериев, пометка «на решение пока не влияет».
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("карточка: блок «Рейтинг клиента» с суммой и критериями", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto(`/applications/${seed.application.id}`);

  const rating = page.getByTestId("client-rating");
  await expect(rating.getByRole("heading", { name: "Рейтинг клиента" })).toBeVisible();
  await expect(rating.getByText("информационно — на решение пока не влияет")).toBeVisible();
  // Плитка на каждый критерий — сразу, с правилом и баллами, без кнопок.
  await expect(rating.locator(".cr-item")).toHaveCount(4);
  for (const criterion of ["Возраст", "Контакты", "Работа", "Прописка"]) {
    await expect(rating.locator(".cr-item__title", { hasText: criterion })).toBeVisible();
  }
  await expect(rating.getByText(/учтено \d из 4 критериев/)).toBeVisible();
  await expect(rating.getByText("баллов", { exact: true })).toBeVisible();
  await expect(rating.getByText(/Пока не учитывается/)).toBeVisible();
});
