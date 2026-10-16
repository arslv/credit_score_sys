// T17, часть 4: быстрые фильтры списка. У риск-менеджера очередь «Требуют
// действия» — расхождение с оператором (он изменил статус в системе заказчика).
// Заявка из сида — стадия А «передано оператору»: она не серая зона, и в
// «Серой зоне» её нет. Счётчики на чипах видны.
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("быстрые фильтры списка: очередь роли, серая зона, счётчики видны", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto("/applications");

  const queue = page.getByRole("button", { name: /Требуют действия · \d+/ });
  await expect(queue).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: /Серая зона · \d+/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Сегодня · \d+/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Все · \d+/ })).toBeVisible();
  // Очередь отсортирована по «ждёт с» (старые сверху) — на первой странице может
  // не оказаться конкретной заявки, поэтому проверяем сам факт строк в очереди.
  await expect(page.locator("tbody tr").first()).toBeVisible();

  await page.getByRole("button", { name: /Серая зона · \d+/ }).click();
  await expect(page.getByText("Оператор изменил статус")).toHaveCount(0);
});

test("наблюдатель не видит чипа «Требуют действия» — очереди у него нет", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.viewer.email, seed.viewer.password);
  await page.goto("/applications");

  await expect(page.getByRole("button", { name: /Все · \d+/ })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: /Требуют действия/ })).toHaveCount(0);
});
