import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("типовые пороги по ПДН: проценты, запятая и точка сохраняются одинаково", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.risk_manager.email, seed.risk_manager.password);
  await page.goto("/rulesets");

  const rejectAbove = page.getByLabel("ПДН (заявленный до БКИ и по КИ): отказ выше (%)");
  const manualAbove = page.getByLabel("ПДН по КИ: ручная проверка выше (%)");
  await expect(rejectAbove).toHaveValue("60");
  await expect(manualAbove).toHaveValue("55");

  const yamlBox = page.locator("textarea");

  // Запятая (ваше замечание) — браузер в русской локали показывает/принимает
  // дробные через запятую, Number("0,75") — NaN без нормализации.
  await manualAbove.fill("62,5");
  await expect(yamlBox).toHaveValue(/value: 0\.625/);

  await rejectAbove.fill("58");
  await expect(yamlBox).toHaveValue(/pti_max: 0\.58/);
});

test("admin не видит раздел «Правила и пороги» и получает «недостаточно прав» по прямому адресу", async ({
  page,
}) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);

  await expect(page.getByRole("link", { name: "Скоринг" })).toHaveCount(0);

  await page.goto("/rulesets");
  await expect(page.getByText("Недостаточно прав.")).toBeVisible();
});
