// Поручитель в ручных проверках: ФИО видно, лишнего текста нет.
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("поручитель: ФИО в ручных проверках", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto(`/applications/${seed.gray_fssp.id}`);

  const action = page.locator(".pf-action");
  await expect(action.getByText(/Осталось|требуется действие/)).toHaveCount(0);
  // ФССП и банкротство — вместе, в блоке действия; у поручителя — ФИО.
  const fssp = action.locator("section", { has: page.locator("#fssp") });
  await expect(fssp.getByText(new RegExp(`Поручитель.*${seed.gray_fssp.guarantor.last_name}`))).toBeVisible();
  await expect(action.locator("#fedresurs")).toBeVisible();

});
