// Поручитель в ручных проверках: ФИО видно, что осталось — поимённо, на
// в «Нужно ваше действие».
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("поручитель: ФИО и что осталось", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto(`/applications/${seed.gray_fssp.id}`);

  const action = page.locator(".pf-action");
  await expect(action.locator(".pf-action__left")).toHaveText(
    "Осталось: ФССП заявителя и поручителя; банкротство заявителя и поручителя",
  );
  // ФССП и банкротство — вместе, в блоке действия; у поручителя — ФИО.
  const fssp = action.locator("section", { has: page.locator("#fssp") });
  await expect(fssp.getByText(new RegExp(`Поручитель.*${seed.gray_fssp.guarantor.last_name}`))).toBeVisible();
  await expect(action.locator("#fedresurs")).toBeVisible();

});
