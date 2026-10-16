// T23: Настройки — подменю слева, создание в модальном окне, поиск в
// таблицах; Справка — оглавление и поиск по терминам.
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("подменю Настроек показывает только разделы роли", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto("/employees");
  const nav = page.getByRole("navigation", { name: "Настройки" });
  for (const item of ["Сотрудники", "Справочники", "Импорт", "Аудит", "Состояние системы"]) {
    await expect(nav.getByRole("link", { name: item })).toBeVisible();
  }
  // T23: «Проверка по перечням» — отдельный раздел меню, не настройка.
  await expect(nav.getByRole("link", { name: "Проверка по перечням" })).toHaveCount(0);

  await page.getByRole("button", { name: "Выйти" }).click();
  await login(page, seed.operator.email, seed.operator.password);
  await expect(page.getByRole("link", { name: "Проверка по перечням" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Настройки" })).toHaveCount(0);
});

test("новый сотрудник создаётся в модальном окне и находится поиском", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto("/employees");

  await page.getByRole("button", { name: "Новый сотрудник" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Новый сотрудник" });
  await expect(dialog).toBeVisible();

  const email = `e2e-modal-${Date.now()}@example.com`;
  await dialog.getByLabel("ФИО").fill("Модальный Тест");
  await dialog.getByLabel("Email").fill(email);
  await dialog.getByRole("combobox").selectOption({ label: "Наблюдатель" });
  await dialog.getByRole("textbox", { name: /^Пароль/ }).fill("e2e-modal-password-123");
  await dialog.getByRole("button", { name: "Создать" }).click();
  await expect(dialog).toHaveCount(0);

  await page.getByRole("searchbox", { name: "Имя или email" }).fill(email);
  const row = page.getByRole("row", { name: new RegExp(email) });
  await expect(row).toBeVisible();
  await expect(row.getByText("Наблюдатель")).toBeVisible();
  await expect(page.getByRole("row")).toHaveCount(2); // заголовок + найденный

  // Esc закрывает окно без сохранения.
  await row.getByRole("button", { name: "Изменить" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("справочники: вкладки и поиск", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto("/references");
  await page.getByRole("searchbox", { name: "Код или название" }).fill("мебель");
  await expect(page.getByRole("cell", { name: "Мебель", exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: "Электроника", exact: true })).toHaveCount(0);

  await page.getByRole("tab", { name: "Причины" }).click();
  await expect(page.getByText("Только просмотр — коды заполняются миграцией.")).toBeVisible();
});

test("справка: поиск по терминам и оглавление", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.operator.email, seed.operator.password);
  await page.goto("/help");

  const toc = page.getByRole("navigation", { name: "Оглавление справки" });
  await expect(toc.getByRole("link", { name: "Термины" })).toBeVisible();

  // Поиск внизу страницы: при фильтрации экран не должен прыгать —
  // поле остаётся на том же месте окна.
  const search = page.getByRole("searchbox", { name: "Найти термин" });
  await search.scrollIntoViewIfNeeded();
  const before = (await search.boundingBox())!.y;
  await search.fill("ПДН");
  await page.waitForTimeout(100);
  expect(Math.abs((await search.boundingBox())!.y - before)).toBeLessThan(2);
  await expect(page.getByText("Найдено:")).toBeVisible();
  await expect(page.getByText("Стоп-фактор", { exact: true })).toHaveCount(0);

  // По словам, а не строкой целиком: «стоп ф» — это «Стоп-фактор».
  await search.fill("стоп ф");
  await expect(page.getByText("Найдено: 1 из 8")).toBeVisible();
  await expect(page.locator("dt").first()).toHaveText("Стоп-фактор");

  await page.getByRole("searchbox", { name: "Найти термин" }).fill("несуществующее слово");
  await expect(page.getByText("Такого термина нет")).toBeVisible();
});
