// T17, часть 6: справка доступна всем ролям, содержит путь заявки, три рычага,
// таблицу ролей и термины — теми же словами, что подсказки Term по интерфейсу.
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

const GLOSSARY_TERMS = [
  "Балл БКИ (скор)", "ПДН", "Серая зона", "Стоп-фактор",
  "Стадия А / стадия Б", "«Не вычислимо»", "БКИ", "ФССП",
];

test("справка: путь заявки, три рычага, роли и все термины на месте", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.risk_manager.email, seed.risk_manager.password);
  await page.getByRole("link", { name: "Справка" }).click();
  await expect(page).toHaveURL(/\/help/);

  await expect(page.getByRole("heading", { name: "Как работает система" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Путь заявки" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Три рычага, которые влияют на решение" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Роли" })).toBeVisible();
  await expect(page.getByText("Риск-менеджер (вы)")).toBeVisible();

  for (const term of GLOSSARY_TERMS) {
    await expect(page.getByText(term, { exact: true })).toBeVisible();
  }
});

test("справка видна каждой роли (оператор)", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.operator.email, seed.operator.password);
  await expect(page.getByRole("link", { name: "Справка" })).toBeVisible();
  await page.goto("/help");
  await expect(page.getByRole("heading", { name: "Как работает система" })).toBeVisible();
  await expect(page.getByText("Оператор (вы)")).toBeVisible();
});
