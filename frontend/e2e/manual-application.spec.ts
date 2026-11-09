import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

// Заявка без CRM (решение владельца 09.11.2026): оператор заводит её сам,
// с фото паспорта; в карточке видно, что в CRM её нет.
const PNG = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(200, 1)]);

test("оператор заводит заявку вручную с фото паспорта", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.operator.email, seed.operator.password);

  await page.goto("/applications");
  await page.getByRole("link", { name: "Новая заявка" }).click();
  await expect(page.getByRole("heading", { name: "Новая заявка" })).toBeVisible();

  await page.getByLabel("Фамилия", { exact: true }).fill("Ручнов");
  await page.getByLabel("Имя", { exact: true }).fill("Тест");
  await page.getByLabel("Телефон", { exact: true }).fill("+79990002233");
  await page.getByLabel("Дата рождения", { exact: true }).fill("1990-05-14");
  await page.getByLabel("Серия", { exact: true }).fill("8215");
  await page.getByLabel("Номер", { exact: true }).fill(String(100000 + Math.floor(Math.random() * 899999)));
  await page.getByLabel("Фото: разворот с фото").setInputFiles({ name: "main.png", mimeType: "image/png", buffer: PNG });
  await page.getByLabel("Товар", { exact: true }).fill("Стиральная машина");
  await page.getByLabel("Цена, ₽").fill("45000");
  await page.getByLabel("Срок, мес.").fill("10");
  await page.getByLabel("Доход в месяц, ₽", { exact: true }).fill("70000");
  await page.getByLabel(/согласие на запрос кредитной истории/).check();
  await page.getByRole("button", { name: "Создать заявку" }).click();

  await expect(page).toHaveURL(/\/applications\/[0-9a-f-]{36}$/);
  await expect(page.getByText("заведена вручную, в CRM её нет")).toBeVisible();
});

test("наблюдатель не видит кнопку «Новая заявка»", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.viewer.email, seed.viewer.password);
  await page.goto("/applications");
  await expect(page.getByRole("heading", { name: "Заявки" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Новая заявка" })).toHaveCount(0);
});
