import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("импорт договоров через форму — загрузка CSV и отчёт о результате", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.risk_manager.email, seed.risk_manager.password);

  await page.goto("/imports");
  await expect(page.getByRole("heading", { name: "Импорт данных" })).toBeVisible();

  const number = `E2E-${Date.now()}`;
  const header = [
    "Номер договора", "Сумма товара", "Наценка", "Итого к оплате", "Первый взнос",
    "Срок мес", "Дата выдачи", "Статус", "Номер заявки", "ФИО", "Телефон",
  ];
  const row = [number, "10000", "0", "10000", "0", "6", "01.01.2026", "Действует", "", "", ""];
  const csv = [header.join(","), row.join(",")].join("\n");

  const [contractsInput] = await page.locator('input[type="file"]').all();
  await contractsInput.setInputFiles({
    name: "contracts.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(csv, "utf-8"),
  });

  await expect(page.getByText(/загружено: 1/)).toBeVisible({ timeout: 10_000 });
});

test("admin видит вкладку «Импорт» в разделе «Портфель»", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);

  // T17, шаг 3: «Импорт» — вкладка внутри раздела «Портфель», не отдельный
  // пункт верхнего меню — появляется, когда раздел открыт (вторичная
  // навигация), не на любой странице.
  await page.goto("/contracts");
  await expect(page.getByRole("link", { name: "Импорт" })).toBeVisible();

  await page.goto("/imports");
  await expect(page.getByRole("heading", { name: "Импорт данных" })).toBeVisible();
});

test("viewer не видит вкладку «Импорт» в разделе «Портфель»", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.viewer.email, seed.viewer.password);

  await page.goto("/contracts");
  await expect(page.getByRole("link", { name: "Договоры" })).toBeVisible(); // раздел открыт, подвкладки видны
  await expect(page.getByRole("link", { name: "Импорт" })).toHaveCount(0);
});
