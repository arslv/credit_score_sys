import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("вход и выход", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);

  await page.getByRole("button", { name: "Выйти" }).click();
  await expect(page).toHaveURL(/\/login/);

  // После выхода карточка недоступна — редирект обратно на /login, а не 401
  // с оставшимся на экране содержимым (персданные не должны быть видны).
  await page.goto(`/applications`);
  await expect(page).toHaveURL(/\/login/);
});
