// T17, часть 2: карточка объясняет отказ правдиво. Заявка с санкционным
// отказом и заморозкой (повторная заморозка — triggered_rules пустой) не должна
// писать «стоп-факторов нет»: причина — текст из reason_code. Сырые данные — в
// свёрнутых «Подробностях», не в основном виде.
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("отказ по перечню объясняется текстом причины, а не «стоп-факторов нет»", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.risk_manager.email, seed.risk_manager.password);

  await page.goto(`/applications/${seed.frozen_match.application_id}`);
  await expect(page.getByText("Совпадение по перечню Росфинмониторинга").first()).toBeVisible();
  await expect(page.getByText("Сработавших стоп-факторов нет")).toHaveCount(0);
  await expect(page.getByText("SANCTIONS_HIT")).toHaveCount(0);
});

test("в основном виде карточки нет сырых снимков; «Подробности» свёрнуты", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.risk_manager.email, seed.risk_manager.password);

  await page.goto(`/applications/${seed.frozen_match.application_id}`);
  const details = page.locator("details", { hasText: "Подробности" });
  await expect(details).toHaveCount(1);
  await expect(details).not.toHaveAttribute("open", /.*/);
  await expect(page.getByRole("heading", { name: "Что проверили" })).toBeVisible();
});
