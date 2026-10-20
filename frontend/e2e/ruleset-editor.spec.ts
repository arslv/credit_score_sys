import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("типовые пороги по ПДН: проценты, запятая и точка сохраняются одинаково", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
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

// T23: ролей три — правила меняет admin; оператор сюда не допускается.
test("оператор не видит раздел «Правила и пороги» и получает «недостаточно прав» по прямому адресу", async ({
  page,
}) => {
  const seed = loadSeed();
  await login(page, seed.operator.email, seed.operator.password);

  await expect(page.getByRole("link", { name: "Скоринг" })).toHaveCount(0);

  await page.goto("/rulesets");
  await expect(page.getByText("Недостаточно прав.")).toBeVisible();
});

// Экспертный режим: рядом с YAML — справочник полей с поиском.
test("справочник полей YAML: поиск находит признаки просрочки по КИ", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto("/rulesets");

  await page.getByText("Экспертный режим — YAML напрямую").click();
  const reference = page.getByRole("complementary", { name: "Справочник полей" });
  await reference.getByLabel("Поиск по справочнику полей").fill("просрочк");
  await expect(reference.getByText("features.bki_max_overdue_6mo / 12mo / 24mo")).toBeVisible();
  await expect(reference.getByText("limits.amount_min / amount_max")).toHaveCount(0);
});

// Клик по полю в справочнике — переход к нему в тексте YAML.
test("справочник полей: клик по полю выделяет его строку в YAML", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto("/rulesets");

  await page.getByText("Экспертный режим — YAML напрямую").click();
  const reference = page.getByRole("complementary", { name: "Справочник полей" });
  await reference.getByLabel("Поиск по справочнику полей").fill("cache_days");
  await reference.getByRole("button", { name: /bki\.cache_days/ }).click();

  const selected = await page.getByLabel("YAML конфига правил").evaluate((el) => {
    const t = el as HTMLTextAreaElement;
    return t.value.slice(t.selectionStart, t.selectionEnd);
  });
  expect(selected.trim()).toMatch(/^cache_days:\s*\d+/);
});
