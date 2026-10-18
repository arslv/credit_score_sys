// Анализ ИИ — пока только заготовка интерфейса: блок виден в карточке
// и прямо говорит, что анализ не проводится и на решение не влияет.
import { test, expect } from "@playwright/test";
import { loadSeed } from "./fixtures";
import { login } from "./login";

test("в карточке заявки есть блок «Анализ ИИ» с пометкой «в разработке»", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.viewer.email, seed.viewer.password);
  await page.goto(`/applications/${seed.gray_fssp.id}`);

  const heading = page.getByRole("heading", { name: "Анализ ИИ" });
  await expect(heading).toBeVisible();
  const section = heading.locator("xpath=ancestor::section[1]");
  await expect(section.getByText("в разработке", { exact: true })).toBeVisible();
  await expect(section.getByText(/Анализ пока не проводится и на решение не влияет/)).toBeVisible();
});
