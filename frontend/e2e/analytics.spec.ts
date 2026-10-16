// T17, часть 5: у каждого графика — заголовок-вывод (не нейтральное название),
// шаги воронки — человеческие подписи без сырых кодов, доступ только
// admin/viewer (T23: ролей три, оператору аналитика не нужна).
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("аналитика: воронка с выводом в заголовке, подписи шагов по-русски", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto("/dashboard");

  await expect(page.getByRole("heading", { name: "Аналитика" })).toBeVisible();
  // Заголовок-вывод воронки — предложение с процентом, не просто «Воронка».
  await expect(page.getByText(/поданных заявок/)).toBeVisible();
  await expect(page.getByText("Поступило")).toBeVisible();
  await expect(page.getByText("Передано оператору")).toBeVisible();
  await expect(page.getByText("Рассмотрено (стадия Б)")).toBeVisible();
  await expect(page.getByText("Прошли стадию А")).toHaveCount(0);
  await expect(page.getByText(/^submitted$|^to_operator$|^stage_b$|^approved$/)).toHaveCount(0);
});

test("бары графиков видимо заполнены, не пустой трек (баг заливки)", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto("/dashboard");

  const fill = page.locator(".bar-fill").first();
  await expect(fill).toBeVisible();
  const box = await fill.boundingBox();
  expect(box, "у .bar-fill должен быть реальный прямоугольник").not.toBeNull();
  expect(box!.width).toBeGreaterThan(0);
});

test("даты и месяцы — по-русски, не ISO; переопределения объясняют разницу двух цифр", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto("/dashboard");

  // Неделя "с ДД.ММ.ГГГГ", не голый ISO "YYYY-MM-DD".
  await expect(page.getByText(/^с \d{2}\.\d{2}\.\d{4}$/).first()).toBeVisible();
  await expect(page.getByText(/^\d{4}-\d{2}-\d{2}$/)).toHaveCount(0);
  // Месяц "октябрь 2026"/"сентябрь 2026" и т.п., не голый "YYYY-MM".
  await expect(page.getByText(/^\d{4}-\d{2}$/)).toHaveCount(0);

  // Поля периода: нативный календарь форматируется по локали ОС браузера
  // (вне нашего контроля) — рядом всегда подпись ДД.ММ.ГГГГ, этим и проверяем.
  await expect(page.getByText(/^\d{2}\.\d{2}\.\d{4}$/).first()).toBeVisible();

  // Вторая цифра блока переопределений — доля ОТ переопределённых, не от всех
  // заявок: подпись явно говорит «из них», а не повторяет первую без объяснения.
  await expect(page.getByText(/из них разошлись с системой/)).toBeVisible();

  // Пункт 5: "Винтажный анализ"/"Когорта"/"ещё не зрела" переименованы.
  await expect(page.getByText("Винтажный анализ", { exact: false })).toHaveCount(0);
  await expect(page.getByRole("columnheader", { name: "Когорта" })).toHaveCount(0);
  await expect(page.getByRole("columnheader", { name: "Месяц выдачи" })).toBeVisible();

  // Заголовок-вывод источников — либо «стабильно», либо называет худший, не
  // «есть хоть один недоступный» (порог — конфиг, не 0).
  await expect(page.getByText(/Источники работают стабильно|Чаще всего недоступен источник/)).toBeVisible();
});

test("аналитика недоступна оператору", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.operator.email, seed.operator.password);
  await expect(page.getByRole("link", { name: "Аналитика" })).toHaveCount(0);
  await page.goto("/dashboard");
  await expect(page.getByText("Не удалось загрузить аналитику.")).toBeVisible();
});
