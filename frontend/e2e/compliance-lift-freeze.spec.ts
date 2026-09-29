import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("комплаенс: снятие заморозки требует причину", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.compliance.email, seed.compliance.password);

  await page.goto("/compliance");
  // Дев-БД накапливает совпадения от прошлых прогонов (список — по всей
  // системе, без фильтра по прогону) — работаем строго с карточкой ИМЕННО
  // этого сида, а не первой попавшейся кнопкой на странице.
  const card = page.locator("article", { hasText: seed.frozen_match.external_id });
  await expect(card).toBeVisible();

  await card.getByRole("button", { name: "Снять заморозку" }).click();
  const confirmButton = card.getByRole("button", { name: "Подтвердить снятие" });
  await expect(confirmButton).toBeDisabled(); // пустая причина — кнопка недоступна

  await card.getByLabel("Причина снятия (обязательна)").fill("клиент подтвердил личность лично");
  await expect(confirmButton).toBeEnabled();
  await confirmButton.click();

  await expect(card.getByText("заморожено")).toHaveCount(0);
  await expect(card.getByText("снято")).toBeVisible();
});

test("risk_manager не видит раздел «Комплаенс»", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.risk_manager.email, seed.risk_manager.password);
  await expect(page.getByRole("link", { name: "Комплаенс" })).toHaveCount(0);
});
