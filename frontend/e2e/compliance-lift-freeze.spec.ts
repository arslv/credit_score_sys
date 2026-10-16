import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

// T23: бывший «Комплаенс» — раздел меню «Проверка по перечням», доступен
// администратору и оператору.
test("проверка по перечням: снятие заморозки требует причину", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.operator.email, seed.operator.password);

  await page.getByRole("link", { name: "Проверка по перечням" }).click();
  await expect(page.getByRole("heading", { name: "Проверка по перечням" })).toBeVisible();
  await expect(page.getByText("Как разобрать совпадение")).toBeVisible();
  // Дев-БД накапливает совпадения от прошлых прогонов (список — по всей
  // системе, без фильтра по прогону) — работаем строго с карточкой ИМЕННО
  // этого сида, а не первой попавшейся кнопкой на странице.
  const card = page.locator("article", { hasText: seed.frozen_match.external_id });
  await expect(card).toBeVisible();

  // T23: «Не он» сохраняет разбор и сразу открывает поле причины снятия
  // (раньше сервер отвечал 409, а экран молчал — «кнопки не работают»).
  await card.getByRole("button", { name: "Не он — пометить ложным" }).click();
  await expect(card.getByText("разбор: ложное совпадение")).toBeVisible();
  await expect(card.getByRole("button", { name: "Не он — пометить ложным" })).toHaveAttribute("aria-pressed", "true");
  await expect(card.getByRole("alert")).toHaveCount(0);
  const confirmButton = card.getByRole("button", { name: "Подтвердить снятие" });
  await expect(confirmButton).toBeDisabled(); // пустая причина — кнопка недоступна

  await card.getByLabel("Причина снятия (обязательна)").fill("клиент подтвердил личность лично");
  await expect(confirmButton).toBeEnabled();
  await confirmButton.click();

  await expect(card.getByText("заморожено")).toHaveCount(0);
  await expect(card.getByText("снято")).toBeVisible();
});

test("администратор видит «Проверку по перечням», наблюдатель — нет", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await expect(page.getByRole("link", { name: "Проверка по перечням" })).toBeVisible();
  await expect(page.getByText("Комплаенс")).toHaveCount(0);

  await page.getByRole("button", { name: "Выйти" }).click();
  await login(page, seed.viewer.email, seed.viewer.password);
  await expect(page.getByRole("link", { name: "Заявки" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Проверка по перечням" })).toHaveCount(0);
});
