// Быстрые фильтры списка (решение владельца 23.11.2026): «Новые» (итога ещё
// нет) — по умолчанию, «Серая зона», «Все». Счётчики на чипах видны. Строка
// кликабельна целиком; «← Назад» из заявки — туда, откуда пришли.
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("быстрые фильтры списка: «Новые», «Серая зона», «Все» — со счётчиками", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto("/applications");

  await expect(page.getByRole("button", { name: /^Новые · \d+/ })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: /^Серая зона · \d+/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Все · \d+/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Требуют действия|Сегодня/ })).toHaveCount(0);

  // В «Новых» нет итоговых статусов.
  await expect(page.locator("tbody tr").first()).toBeVisible();
  await expect(page.locator("tbody .status-pill", { hasText: /^(Отказ|Одобрено|Серая зона)$/ })).toHaveCount(0);

  await page.getByRole("button", { name: /^Серая зона · \d+/ }).click();
  await expect(page.locator("tbody .status-pill").first()).toHaveText("Серая зона");
});

test("наблюдатель: те же три вкладки", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.viewer.email, seed.viewer.password);
  await page.goto("/applications");

  await expect(page.getByRole("button", { name: /^Новые · \d+/ })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: /^Все · \d+/ })).toBeVisible();
});

test("клик по строке открывает заявку", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto("/applications");
  await page.getByRole("button", { name: /^Все · \d+/ }).click();

  await page.locator("tbody tr").first().locator("td.num").first().click();
  await expect(page).toHaveURL(/\/applications\/[0-9a-f-]{36}$/);
});

test("«← Назад» из заявки возвращает к клиенту, откуда пришли", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  const clientUrl = `/clients/${seed.frozen_match.client_id}`;
  await page.goto(clientUrl);

  await page.locator(`a[href="/applications/${seed.frozen_match.application_id}"]`).first().click();
  await expect(page).toHaveURL(new RegExp(`/applications/${seed.frozen_match.application_id}$`));
  await page.getByRole("button", { name: "← Назад" }).click();
  await expect(page).toHaveURL(new RegExp(`${clientUrl}$`));
});

test("«← Назад» из заявки возвращает в ту же вкладку и на ту же страницу", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto("/applications");

  await page.getByRole("button", { name: /^Серая зона · \d+/ }).click();
  await expect(page).toHaveURL(/view=gray/);
  await page.locator("tbody tr").first().locator("td.num").first().click();
  await expect(page).toHaveURL(/\/applications\/[0-9a-f-]{36}$/);
  await page.getByRole("button", { name: "← Назад" }).click();
  await expect(page.getByRole("button", { name: /^Серая зона · \d+/ })).toHaveAttribute("aria-pressed", "true");

  // Страница тоже сохраняется.
  await page.getByRole("button", { name: /^Все · \d+/ }).click();
  await page.getByRole("button", { name: /вперёд/ }).click();
  await expect(page).toHaveURL(/view=all.*page=2|page=2.*view=all/);
  await page.locator("tbody tr").first().locator("td.num").first().click();
  await page.getByRole("button", { name: "← Назад" }).click();
  await expect(page.getByText(/Страница 2 из/)).toBeVisible();
  await expect(page.getByRole("button", { name: /^Все · \d+/ })).toHaveAttribute("aria-pressed", "true");
});
