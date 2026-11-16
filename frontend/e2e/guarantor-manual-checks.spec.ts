// Поручитель в ручных проверках: строки ФССП и Федресурса у поручителя —
// в «Проверках» → «Долги и банкротство», с его ФИО и кнопками под строкой.
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("поручитель: ФИО и кнопки в «Долгах и банкротстве»", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto(`/applications/${seed.gray_fssp.id}`);

  const debts = page.locator(".chk__area--debts");
  const who = new RegExp(`поручитель · ${seed.gray_fssp.guarantor.last_name}`);
  const guarantorRows = debts.locator(".chk__row", { hasText: who });
  // ФССП и Федресурс — у поручителя по строке, у каждой свои кнопки.
  await expect(guarantorRows).toHaveCount(2);
  await expect(guarantorRows.filter({ hasText: "ФССП" }).getByRole("button", { name: /Проверить/ })).toBeVisible();
  await expect(guarantorRows.filter({ hasText: "Федресурс" }).getByRole("button", { name: "Банкротства нет" })).toBeVisible();
  // Отдельного блока «Ручные проверки» нет.
  await expect(page.locator(".pf-action")).toHaveCount(0);
});
