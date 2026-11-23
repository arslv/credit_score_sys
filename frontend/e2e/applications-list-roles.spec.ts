// Список заявок как в CRM (решение владельца 23.11.2026): id, точка продаж,
// клиент, продукт, срок, сумма, анкет, время принятия, статус, действия.
// Что сделать и сколько ждёт — второй строкой под статусом (в очереди роли).
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

const CRM_COLUMNS = [
  "id", "Точка продаж", "Клиент", "Продукт", "Срок", "Сумма", "Анкет", "Время принятия", "Статус", "Действия",
];

test("оператор: очередь грузится, колонки как в CRM", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.operator.email, seed.operator.password);
  await page.goto("/applications");

  await expect(page.getByRole("button", { name: /^Новые · \d+/ })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("Загрузка…")).toHaveCount(0, { timeout: 10_000 });
  await expect(page.getByRole("table")).toBeVisible({ timeout: 10_000 });
  const headers = await page.getByRole("columnheader").allTextContents();
  expect(headers.map((h) => h.trim())).toEqual(CRM_COLUMNS);
});

test("наблюдатель: список тот же", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.viewer.email, seed.viewer.password);
  await page.goto("/applications");

  await expect(page.getByRole("table")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("columnheader", { name: "Статус" })).toBeVisible();
});

test("администратор: в очереди под статусом — что сделать и сколько ждёт; меню «⋮» открывает заявку", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto("/applications");
  await page.getByRole("button", { name: /^Серая зона · \d+/ }).click();

  await expect(page.getByRole("table")).toBeVisible({ timeout: 10_000 });
  const firstRow = page.locator("tbody tr").first();
  await expect(firstRow.locator(".cell-status__sub").first()).toHaveText(/· \d+ (мин|ч|дн)/);

  await firstRow.getByRole("button", { name: "Действия с заявкой" }).click();
  // Меню не обрезается рамкой таблицы — видно целиком (отзыв 24.11.2026).
  await expect(page.getByRole("menuitem", { name: "Изменить анкету" })).toBeInViewport({ ratio: 1 });
  await page.getByRole("menuitem", { name: "Открыть заявку" }).click();
  await expect(page).toHaveURL(/\/applications\/[0-9a-f-]{36}$/);
});
