// T23: банкротство (Федресурс) — ручная проверка рядом с ФССП: напоминание
// в «Нужно сделать», переход с подсветкой, отметка результата и повтор.
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("оператор отмечает проверку банкротства; найдено — только с описанием", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.operator.email, seed.operator.password);
  await page.goto(`/applications/${seed.no_income.id}`);

  await page.getByRole("link", { name: "К проверке банкротства" }).click();
  const section = page.locator("#fedresurs").locator("xpath=ancestor::section[1]");
  await expect(section).toHaveClass(/section--flash/);
  await expect(section.getByText("не проверено")).toBeVisible();
  await expect(section.getByRole("link", { name: /bankrot\.fedresurs\.ru/ })).toHaveAttribute(
    "href", "https://bankrot.fedresurs.ru/",
  );

  await section.getByRole("button", { name: "Найдено банкротство" }).click();
  const save = section.getByRole("button", { name: "Сохранить" });
  await expect(save).toBeDisabled(); // без описания не сохранить
  await section.getByLabel(/Что найдено/).fill("дело А15-1/2026, реструктуризация долгов");
  await save.click();
  await expect(section.getByText("найдено банкротство")).toBeVisible();
  await expect(section.getByText("Что найдено: дело А15-1/2026, реструктуризация долгов")).toBeVisible();
  await expect(page.getByRole("link", { name: "К проверке банкротства" })).toHaveCount(0);

  await section.getByRole("button", { name: "Проверить заново" }).click();
  await section.getByRole("button", { name: "Банкротства нет" }).click();
  await expect(section.getByText("банкротства нет")).toBeVisible();
});

test("наблюдатель видит статус банкротства, но не отмечает", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.viewer.email, seed.viewer.password);
  await page.goto(`/applications/${seed.gray_fssp.id}`);
  const section = page.locator("#fedresurs").locator("xpath=ancestor::section[1]");
  await expect(section.getByText("Проверяет оператор или администратор.")).toBeVisible();
  await expect(section.getByRole("button", { name: "Банкротства нет" })).toHaveCount(0);
});
