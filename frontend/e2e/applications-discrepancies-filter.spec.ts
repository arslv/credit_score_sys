import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

// Фильтры видны сразу, кнопками (отзыв 24.11.2026: без «Ещё фильтры»);
// включённый фильтр остаётся в адресе и сбрасывается одной кнопкой.
test("список заявок — фильтр «Оператор изменил статус» кнопкой, сброс", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto("/applications");

  await expect(page.getByText("Ещё фильтры")).toHaveCount(0);
  // «Все» — сортировка по дате подачи (новые сверху), свежая сидовая заявка — первая.
  await page.getByRole("button", { name: /^Все · \d+/ }).click();
  const pill = page.getByRole("button", { name: "Оператор изменил статус" });
  await pill.click();
  await expect(pill).toHaveAttribute("aria-pressed", "true");
  await expect(page).toHaveURL(/disc=1/);
  await expect(page.getByText(seed.application.external_id)).toBeVisible();
  await expect(page.locator("tbody .cell-status__sub", { hasText: "Оператор изменил статус" }).first()).toBeVisible();

  await page.getByRole("button", { name: "Сбросить фильтры" }).click();
  await expect(page.getByRole("button", { name: /Оператор изменил статус/ })).toHaveAttribute("aria-pressed", "false");
  await expect(page).not.toHaveURL(/disc=1/);
});

test("список заявок — фильтр по статусу списком", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto("/applications?view=all");

  await page.getByLabel("Статус").selectOption({ label: "Серая зона" });
  await expect(page).toHaveURL(/status=gray_zone/);
  await expect(page.locator("tbody .status-pill").first()).toHaveText("Серая зона");
});
