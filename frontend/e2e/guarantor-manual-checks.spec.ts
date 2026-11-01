// Поручитель в ручных проверках: ФИО видно, что осталось — поимённо, на
// шкале и в «Нужно ваше действие»; проверки прежнего поручителя (до
// повторного «Рассмотреть») в ленте не дублируются.
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("поручитель: ФИО, что осталось и без дублей в ленте", async ({ page }) => {
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

  // Шкала: не «проверены», а что именно осталось.
  const step = page.locator(".journey__step", { hasText: "ФССП и банкротство" });
  await expect(step).toContainText("ФССП заявителя и поручителя");
  await expect(step).not.toContainText("проверены");

  // В ленте ФССП поручителя — один раз (прежний поручитель не учитывается).
  await expect(page.locator(".ev__chip", { hasText: "ФССП (поручитель)" })).toHaveCount(1);
});
