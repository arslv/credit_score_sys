// T22: публикация и откат версии правил — на ОТДЕЛЬНОЙ базе dialog_e2e
// (проект isolated-db в playwright.config.ts). Версии правил append-only:
// в dev-базе после этого теста активной осталась бы новая версия (копия при
// откате), и global-teardown.ts уронил бы прогон.
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("правила и пороги: изменил порог → симуляция → сохранил → откатил с причиной", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.risk_manager.email, seed.risk_manager.password);

  await page.goto("/rulesets");
  await expect(page.getByText(/Активная версия: \d+/)).toBeVisible();

  const activeVersionText = await page.getByText(/Активная версия: \d+/).innerText();
  const activeVersion = Number(activeVersionText.match(/\d+/)![0]);

  const autoRejectBelow = page.getByLabel("Автоотказ ниже");
  await expect(autoRejectBelow).toBeVisible();
  const original = await autoRejectBelow.inputValue();
  await autoRejectBelow.fill(String(Number(original) + 1));

  await page.getByRole("button", { name: "Проверить на истории" }).click();
  await expect(page.getByText(/В выборке \d+/)).toBeVisible({ timeout: 15_000 });

  // T17, шаг 3: публикация требует комментарий — без него кнопка неактивна.
  const saveButton = page.getByRole("button", { name: "Опубликовать" });
  await expect(saveButton).toBeDisabled();
  await page.getByLabel("Комментарий к версии (обязательно)").fill("e2e: поднял порог отказа для проверки");
  await expect(saveButton).toBeEnabled();
  await saveButton.click();

  const savedVersionText = await page.getByText(/Сохранено как версия \d+\./).innerText();
  const savedVersion = Number(savedVersionText.match(/\d+/)![0]);
  expect(savedVersion).toBeGreaterThan(activeVersion);

  // История версий — откатываемся к версии ДО этого изменения, с обязательной
  // причиной. Матчим по ТОЧНОМУ тексту первой ячейки (номер версии), не по
  // произвольному вхождению подстроки — иначе легко попасть в дату/комментарий.
  const historyRow = page.getByRole("row").filter({
    has: page.getByRole("cell", { name: String(activeVersion), exact: true }),
  });
  await historyRow.getByRole("button", { name: "Откатить" }).click();

  const confirmRollback = page.getByRole("button", { name: "Подтвердить откат" });
  await expect(confirmRollback).toBeDisabled(); // причина обязательна

  await page.getByLabel("Причина").fill("e2e: проверка отката с обязательной причиной");
  await expect(confirmRollback).toBeEnabled();
  await confirmRollback.click();

  const rollbackVersionText = await page.getByText(/Откат сохранён как версия \d+\./).innerText();
  const rollbackVersion = Number(rollbackVersionText.match(/\d+/)![0]);
  expect(rollbackVersion).toBeGreaterThan(savedVersion);
});
