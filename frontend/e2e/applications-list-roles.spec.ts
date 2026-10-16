// T17, часть 4 (баги после скриншотов): у каждой роли список реально грузится
// (таблица появляется, не «Загрузка…»), а колонки, которые роли всегда пусты,
// не показываются. T23: ролей три, очереди нет только у наблюдателя.
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("оператор: очередь «Требуют действия» грузится и показывает таблицу", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.operator.email, seed.operator.password);
  await page.goto("/applications");

  await expect(page.getByRole("button", { name: /Требуют действия · \d+/ })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("Загрузка…")).toHaveCount(0, { timeout: 10_000 });
  await expect(page.getByRole("table")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("columnheader", { name: "Нужно сделать" })).toBeVisible();
});

test("наблюдатель: без очереди, колонок «Нужно сделать» и «Ждёт» нет", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.viewer.email, seed.viewer.password);
  await page.goto("/applications");

  await expect(page.getByRole("table")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("button", { name: /Требуют действия/ })).toHaveCount(0);
  await expect(page.getByRole("columnheader", { name: "Нужно сделать" })).toHaveCount(0);
  await expect(page.getByRole("columnheader", { name: "Ждёт" })).toHaveCount(0);
});

test("администратор: «Ждёт» в очереди показывает время ожидания", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto("/applications");

  await expect(page.getByRole("table")).toBeVisible({ timeout: 10_000 });

  const waitIndex = await page.getByRole("columnheader").evaluateAll(
    (headers) => headers.findIndex((th) => th.textContent?.trim() === "Ждёт"),
  );
  expect(waitIndex).toBeGreaterThanOrEqual(0);
  const firstWait = page.locator("tbody tr").first().locator("td").nth(waitIndex);
  await expect(firstWait).toHaveText(/\d+ (мин|ч|дн)/);
});
