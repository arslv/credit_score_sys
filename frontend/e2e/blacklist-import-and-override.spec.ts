// T23: чёрный список загружается файлом; решение оператора заказчика в
// карточке — обычными словами, без кодов статусов.
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("чёрный список: загрузка CSV, повторная загрузка не плодит дубли", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto("/references");
  await page.getByRole("tab", { name: "Чёрный список" }).click();

  // Только фамилия и причина: без паспорта и телефона строка ни с кем не совпадёт
  // (совпадение — по паспорту или телефону), dev-база от теста не страдает.
  const lastName = `Импортов${Date.now()}`;
  const csv = `last_name;first_name;reason\n${lastName};Тест;проверка загрузки (синтетические данные)\n`;
  const upload = async () => {
    await page.getByRole("button", { name: "Загрузить из файла" }).click();
    const dialog = page.getByRole("dialog", { name: "Загрузить чёрный список из файла" });
    await dialog.getByLabel("Файл").setInputFiles({
      name: "blacklist.csv", mimeType: "text/csv", buffer: Buffer.from(csv, "utf-8"),
    });
    await dialog.getByRole("button", { name: "Загрузить" }).click();
    return dialog;
  };

  const first = await upload();
  await expect(first.getByRole("status")).toHaveText("Добавлено: 1");
  await first.getByRole("button", { name: "Готово" }).click();
  await page.getByRole("searchbox").fill(lastName);
  await expect(page.getByRole("cell", { name: `${lastName} Тест` })).toBeVisible();

  const second = await upload();
  await expect(second.getByRole("status")).toHaveText("Добавлено: 0 · уже были в списке: 1");
});

test("решение оператора заказчика — что решила система, итог оператора и причина", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto(`/applications/${seed.application.id}`);

  await expect(page.getByRole("heading", { name: "Решение оператора заказчика" })).toBeVisible();
  const section = page.locator(".override").first();
  await expect(section.getByText("Оператор заказчика одобрил заявку вручную")).toBeVisible();
  await expect(section.getByText("Решение системы")).toBeVisible();
  await expect(section.getByText("Итог оператора")).toBeVisible();
  await expect(section.getByText("Одобрено")).toBeVisible();
  await expect(section.getByText(/manual_|approve\b/)).toHaveCount(0);
});
