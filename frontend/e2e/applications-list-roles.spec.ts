// T17, часть 4 (баги после скриншотов): у каждой роли список реально грузится
// (таблица появляется, не «Загрузка…»), а колонки, которые роли всегда пусты,
// не показываются. У комплаенса «Ждёт» — время с момента заморозки.
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

test("комплаенс: «Ждёт» показывает время с заморозки; «Балл БКИ» и «Итог оператора» скрыты", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.compliance.email, seed.compliance.password);
  await page.goto("/applications");

  await expect(page.getByRole("table")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("columnheader", { name: "Балл БКИ" })).toHaveCount(0);
  await expect(page.getByRole("columnheader", { name: "Итог оператора" })).toHaveCount(0);

  const waitIndex = await page.getByRole("columnheader").evaluateAll(
    (headers) => headers.findIndex((th) => th.textContent?.trim() === "Ждёт"),
  );
  expect(waitIndex).toBeGreaterThanOrEqual(0);
  const firstWait = page.locator("tbody tr").first().locator("td").nth(waitIndex);
  await expect(firstWait).toHaveText(/\d+ (мин|ч|дн)/);
});
