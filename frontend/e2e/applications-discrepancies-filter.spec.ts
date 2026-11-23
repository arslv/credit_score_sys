import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

// Фильтр списка — один «Статус» в строке с вкладками (отзыв 24.11.2026:
// кнопки «Оператор изменил статус» и т. п. убраны). Остаётся в адресе,
// сбрасывается одной кнопкой.
test("список заявок — фильтр по статусу и сброс", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto("/applications?view=all");

  await expect(page.getByText("Ещё фильтры")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Оператор изменил статус" })).toHaveCount(0);

  await page.getByLabel("Статус").selectOption({ label: "Серая зона" });
  await expect(page).toHaveURL(/status=gray_zone/);
  await expect(page.locator("tbody .status-pill").first()).toHaveText("Серая зона");

  await page.getByRole("button", { name: "Сбросить" }).click();
  await expect(page).not.toHaveURL(/status=/);
  await expect(page.getByLabel("Статус")).toHaveValue("");
});
