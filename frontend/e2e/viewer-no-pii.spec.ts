import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("viewer не видит ФИО заявителя", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.viewer.email, seed.viewer.password);

  await expect(page.getByText("Клиент скрыт").first()).toBeVisible();
  await expect(page.getByText(seed.application.last_name)).toHaveCount(0);

  await page.goto(`/applications/${seed.application.id}`);
  await expect(page.getByText(seed.application.last_name)).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Анкета" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Распознавание" })).toHaveCount(0);
});
