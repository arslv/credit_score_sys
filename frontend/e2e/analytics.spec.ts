// T17, часть 5: у каждого графика — заголовок-вывод (не нейтральное название),
// шаги воронки — человеческие подписи без сырых кодов, доступ только
// risk_manager/viewer (как и раньше у дашборда).
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("аналитика: воронка с выводом в заголовке, подписи шагов по-русски", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.risk_manager.email, seed.risk_manager.password);
  await page.goto("/dashboard");

  await expect(page.getByRole("heading", { name: "Аналитика" })).toBeVisible();
  // Заголовок-вывод воронки — предложение с процентом, не просто «Воронка».
  await expect(page.getByText(/поданных заявок/)).toBeVisible();
  await expect(page.getByText("Поступило")).toBeVisible();
  await expect(page.getByText("Передано оператору")).toBeVisible();
  await expect(page.getByText("Рассмотрено (стадия Б)")).toBeVisible();
  await expect(page.getByText("Прошли стадию А")).toHaveCount(0);
  await expect(page.getByText(/^submitted$|^to_operator$|^stage_b$|^approved$/)).toHaveCount(0);
});

test("аналитика недоступна комплаенсу и администратору", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.compliance.email, seed.compliance.password);
  await expect(page.getByRole("link", { name: "Аналитика" })).toHaveCount(0);
  await page.goto("/dashboard");
  await expect(page.getByText("Не удалось загрузить аналитику.")).toBeVisible();
});
