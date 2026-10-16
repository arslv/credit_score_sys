import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

// T23 (решение владельца): персданные не скрываются ни у одной роли —
// наблюдатель видит ФИО в списке и анкету в карточке, но ничего не меняет.
test("наблюдатель видит ФИО заявителя и анкету", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.viewer.email, seed.viewer.password);

  await page.goto(`/applications/${seed.application.id}`);
  await expect(page.getByText(seed.application.last_name).first()).toBeVisible();
  await expect(page.getByText("Клиент скрыт")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Повторить/ })).toHaveCount(0);
});
