// Тема оформления: переключатель внизу меню — светлая / тёмная / как в системе;
// выбор запоминается в браузере и переживает перезагрузку.
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("переключатель темы: тёмная запоминается, «как в системе» сбрасывает", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.viewer.email, seed.viewer.password);
  await page.goto("/applications");

  const html = page.locator("html");
  await page.getByRole("radio", { name: "Тёмная" }).click();
  await expect(html).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(html).toHaveAttribute("data-theme", "dark");
  await expect(page.getByRole("radio", { name: "Тёмная" })).toHaveAttribute("aria-checked", "true");

  await page.getByRole("radio", { name: "Как в системе" }).click();
  await expect(html).not.toHaveAttribute("data-theme", /.+/);
});
