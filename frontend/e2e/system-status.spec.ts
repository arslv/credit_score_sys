// T20: «Состояние системы» — одна строка-итог сверху и проблемы
// человеческим языком; технические счётчики — в свёрнутых «Подробностях».
import { test, expect } from "@playwright/test";
import { healthProblems, type HealthInput } from "../src/lib/health";
import { loadSeed } from "./fixtures";
import { login } from "./login";

const healthy: HealthInput = {
  ruleset_active_version: 3,
  ruleset_matches_file: true,
  bki_configured: true,
  fssp_results_after_decision: 0,
  partner_integration_errors_last_24h: 0,
  stuck_applications_over_1h: 0,
  stuck_in_ocr: 0,
  stage_b_reprocessing_loop_applications: 0,
  registries: [
    { registry: "rosfinmon_sanctions", stale: false },
    // МВД отменён в 2023 и всегда «устарел» — проблемой не считается.
    { registry: "mvd_invalid_passports", stale: true },
  ],
};

test("всё в порядке — проблем нет", () => {
  expect(healthProblems(healthy)).toEqual([]);
});

test("каждая проблема — что случилось и что делать, без технических кодов", () => {
  const problems = healthProblems({
    ...healthy,
    bki_configured: false,
    stuck_in_ocr: 2,
    stuck_applications_over_1h: 5,
    registries: [{ registry: "rosfinmon_sanctions", stale: true }],
  });
  expect(problems.map((p) => p.key)).toEqual(["bki", "ocr", "stuck", "registry-rosfinmon_sanctions"]);
  expect(problems[1]!.what).toBe("2 заявки зависли в распознавании паспорта.");
  expect(problems[2]!.what).toBe("5 заявок больше часа без движения.");
  for (const p of problems) {
    expect(p.todo.length).toBeGreaterThan(0);
    expect(`${p.what} ${p.todo}`).not.toMatch(/rosfinmon|stuck|_/);
  }
});

test("экран: строка-итог сверху, подробности свёрнуты", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto("/system");

  await expect(page.getByRole("heading", { name: "Состояние системы" })).toBeVisible();
  await expect(page.getByTestId("health-summary")).toHaveText(/^(Всё работает|Есть проблемы: \d+)$/);
  const details = page.locator("details", { hasText: "Подробности" });
  await expect(details).not.toHaveAttribute("open", /.*/);
  await expect(page.getByText("Платных запросов в БКИ за месяц")).not.toBeVisible();
  await details.locator("summary").click();
  await expect(page.getByText("Платных запросов в БКИ за месяц")).toBeVisible();
});
