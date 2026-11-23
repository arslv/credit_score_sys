// ФССП с разворотом (действующие и оконченные производства), «В чёрный
// список» из заявки и выгрузка чёрного списка.
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("ФССП: производства разворачиваются, у оконченного — статья простыми словами", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto(`/applications/${seed.application.id}`);

  const toggle = page.getByRole("button", { name: /^Производства: 1 действующее · 1 оконченное/ });
  await expect(toggle).toBeVisible();
  await toggle.click();
  await expect(page.getByText("Действующие", { exact: true })).toBeVisible();
  await expect(page.getByText(/Окончено 15\.06\.2023: не нашли должника или имущество/)).toBeVisible();
  await expect(page.getByText("(ст. 46 ч. 1 п. 3)")).toBeVisible();
});

test("«В чёрный список» из заявки — данные подставлены, нужна только причина", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto(`/applications/${seed.application.id}`);

  await page.getByRole("button", { name: "В чёрный список" }).click();
  const dialog = page.getByRole("dialog", { name: "Добавить в чёрный список" });
  await expect(dialog.getByText("+79990000000")).toBeVisible();
  const add = dialog.getByRole("button", { name: "Добавить" });
  await expect(add).toBeDisabled(); // без причины нельзя
  await dialog.getByLabel("Причина").fill("проверка (синтетические данные)");
  await add.click();
  await expect(dialog.getByRole("status")).toContainText("Клиент добавлен в чёрный список");
});

test("чёрный список выгружается файлом", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto("/references");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Выгрузить в файл" }).click();
  expect((await download).suggestedFilename()).toMatch(/^blacklist-\d{8}\.csv$/);
});
