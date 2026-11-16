// T23: чёрный список загружается файлом; решение оператора заказчика в
// карточке — обычными словами, без кодов статусов.
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("чёрный список: загрузка CSV, повторная загрузка не плодит дубли", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto("/references");

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

test("решение оператора CRM — исход цветом, без «Система: …» в шапке", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto(`/applications/${seed.application.id}`);

  // Шапка (16.11.2026): исход — цветом полосы, слово — только для экранного
  // диктора; строки «Система: …» нет.
  await expect(page.getByRole("heading", { level: 1, name: /Одобрено оператором/ })).toHaveCount(1);
  await expect(page.locator(".ah")).not.toContainText("Система:");
  await expect(page.locator(".ah").getByText(/manual_|approve\b/)).toHaveCount(0);
});
