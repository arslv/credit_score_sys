// ФССП с разворотом (действующие и оконченные производства), «В чёрный
// список» из заявки и выгрузка чёрного списка.
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("ФССП: производства разворачиваются, у оконченного — статья простыми словами", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto(`/applications/${seed.application.id}`);

  // Под строкой «сайт» (наша проверка) — своя ссылка, под строкой «БКИ» — своя:
  // в ФССП первая — сайт, вторая — бюро.
  const fssp = page.locator(".chk__row", { hasText: "ФССП" });
  const site = fssp.locator(".chk__line-details").first();
  const toggle = site.getByRole("button", { name: "Показать производства (1 действ. · 1 оконч.)" });
  await expect(toggle).toBeVisible();
  await toggle.click();
  await expect(page.getByText("Действующие", { exact: true })).toBeVisible();
  await expect(page.getByText(/Окончено 15\.06\.2023: не нашли должника или имущество/)).toBeVisible();
  await expect(page.getByText("(ст. 46 ч. 1 п. 3)")).toBeVisible();
});

test("ФССП от бюро: «Подробнее» — действующие и оконченные производства", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto(`/applications/${seed.application.id}`);

  const bureau = page.locator(".chk__row", { hasText: "ФССП" }).locator(".chk__line-details").nth(1);
  await bureau.getByRole("button", { name: "Показать производства (1 действ. · 1 оконч.)" }).click();
  await expect(bureau.getByText("Страховые взносы, включая пени")).toBeVisible();
  await expect(bureau.getByText("Окончено 10.02.2017")).toBeVisible();
  await expect(bureau.getByText(/ИП 01010\/11\/10101-ИП от 08\.11\.2016/)).toBeVisible();
});

test("кредитная история: «Показать кредиты» — действующие, закрытые, просрочки за историю", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto(`/applications/${seed.application.id}`);

  await page.getByRole("button", { name: "Показать кредиты (действующих: 3–4 · закрытых видов: 1)" }).click();
  const body = page.locator(".crd__body");
  await expect(body.getByText("Потребительский кредит")).toBeVisible();
  await expect(body.getByText("Ипотека")).toBeVisible();
  await expect(body.locator(".crd__row", { hasText: "Худшая просрочка за всю историю" })).toContainText(
    "Просрочка от 30 до 59 дней",
  );
  await expect(body.locator(".crd__row", { hasText: "Плохие долги" })).toContainText("не было");
});

test("проверки бюро: розыск — в «Перечнях», суды и залоги — в «Долгах и банкротстве»", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto(`/applications/${seed.application.id}`);

  const lists = page.locator(".chk__area--lists");
  await expect(lists.locator(".chk__row", { hasText: "Розыск МВД" })).toContainText("в розыске МВД не числится");
  const debts = page.locator(".chk__area--debts");
  await expect(debts.locator(".chk__row", { hasText: "Суды общей юрисдикции" })).toContainText("дел в судах: 2");
  await expect(debts.locator(".chk__row", { hasText: "Залоги" })).toContainText("имущество клиента в залоге: 1");
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
