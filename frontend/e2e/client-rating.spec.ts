// T23: рейтинг клиента (скоркарта заказчика) в карточке заявки — сумма,
// разбивка по критериям, пометка «на решение пока не влияет».
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("карточка: блок «Рейтинг клиента» с суммой и критериями", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.risk_manager.email, seed.risk_manager.password);
  await page.goto(`/applications/${seed.application.id}`);

  const rating = page.getByTestId("client-rating");
  await expect(rating.getByRole("heading", { name: "Рейтинг клиента" })).toBeVisible();
  await expect(rating.getByText("информационно — на решение пока не влияет")).toBeVisible();
  // Плашки критериев видны сразу, таблица с правилами — по кнопке.
  await expect(rating.locator(".rating-chip")).toHaveCount(4);
  await rating.getByRole("button", { name: "Разбивка по критериям" }).click();
  for (const criterion of ["Возраст", "Контакты", "Работа", "Прописка"]) {
    await expect(rating.getByRole("cell", { name: criterion, exact: true })).toBeVisible();
  }
  await expect(rating.getByText("баллов", { exact: true })).toBeVisible();
  await expect(rating.getByText(/Пока не учитывается/)).toBeVisible();
});
