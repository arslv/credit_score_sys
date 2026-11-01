// T23: банкротство (Федресурс) — ручная проверка рядом с ФССП: пометка
// «требуется действие» у раздела, отметка результата и повтор.
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("оператор отмечает проверку банкротства; найдено — только с описанием", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.operator.email, seed.operator.password);
  await page.goto(`/applications/${seed.no_income.id}`);

  const section = page.locator("#fedresurs").locator("xpath=ancestor::section[1]");
  await expect(section.getByText("требуется действие", { exact: true })).toBeVisible();
  await expect(section.getByText("не проверено")).toBeVisible();
  // Ссылка сразу открывает поиск с подставленным ИНН или ФИО заявителя.
  const href = await section.getByRole("link", { name: /Найти на Федресурсе/ }).getAttribute("href");
  const url = new URL(href ?? "");
  expect(url.origin + url.pathname).toBe("https://bankrot.fedresurs.ru/bankrupts");
  const query = url.searchParams.get("searchString") ?? "";
  expect(query.length).toBeGreaterThan(0);
  await expect(section.locator(".manual-check__query").first()).toHaveText(query);

  await section.getByRole("button", { name: "Найдено банкротство" }).click();
  const save = section.getByRole("button", { name: "Сохранить" });
  await expect(save).toBeDisabled(); // без описания не сохранить
  await section.getByLabel(/Что найдено/).fill("дело А15-1/2026, реструктуризация долгов");
  await save.click();
  await expect(section.getByText("найдено банкротство")).toBeVisible();
  await expect(section.getByText("Что найдено: дело А15-1/2026, реструктуризация долгов")).toBeVisible();
  await expect(section.getByText("требуется действие", { exact: true })).toHaveCount(0);

  await section.getByRole("button", { name: "Проверить заново" }).click();
  await section.getByRole("button", { name: "Банкротства нет" }).click();
  await expect(section.getByText("банкротства нет")).toBeVisible();
});

test("наблюдатель видит статус банкротства, но не отмечает", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.viewer.email, seed.viewer.password);
  await page.goto(`/applications/${seed.gray_fssp.id}`);
  const section = page.locator("#fedresurs").locator("xpath=ancestor::section[1]");
  // У заявки с поручителем — по строке на заявителя и поручителя.
  await expect(section.getByText("Проверяет оператор или администратор.").first()).toBeVisible();
  await expect(section.getByRole("button", { name: "Банкротства нет" })).toHaveCount(0);
});
