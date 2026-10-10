// T20: блок «Почему такое решение» — «Причина» (что реально привело к
// решению) отдельно от «В норме» (пройденные сравнения). Баг был: пройденные
// проверки (балл выше порога, ПДН в норме) шли списком «Сработавшие
// стоп-факторы» с красными точками. Плюс ПДН: «не вычислимо», а не 0%, когда
// дохода нет; одна подсказка «?» на экран — у вердикта, объясняет это решение.
import { test, expect } from "@playwright/test";
import { explainDecision, type DecisionForExplain } from "../src/lib/explain";
import { loadSeed } from "./fixtures";
import { login } from "./login";

const base: DecisionForExplain = {
  stage: "B",
  outcome: "approve",
  reason_code: null,
  reason_text: null,
  triggered_rules: [],
  triggered_rules_text: [],
  score: "780.00",
  score_auto_approve_above: 700,
  score_auto_reject_below: 450,
  pti: 0.31,
  pti_max: 0.6,
  pti_status: "computed",
  bki_active_contracts_count: 2,
};

test("одобрение: пройденные сравнения — «В норме», причин нет", () => {
  const e = explainDecision(base);
  expect(e.causes).toEqual([]);
  expect(e.passed).toEqual([
    "Балл БКИ 780 — не ниже порога одобрения 700",
    "ПДН 31% — в пределах порога 60%",
  ]);
  expect(e.help).toBe("Одобрено: балл 780 выше порога 700 и ПДН 31% в пределах 60%, стоп-факторов нет.");
});

test("серая зона из-за ФССП: причина одна (в вердикте), балл и ПДН — в норме", () => {
  const e = explainDecision({
    ...base,
    outcome: "manual_review",
    reason_code: "FSSP_NOT_CHECKED",
    reason_text: "Проверка ФССП не пройдена (заявитель или поручитель), решение за оператором.",
    score: 760,
  });
  expect(e.reason).toContain("Проверка ФССП не пройдена");
  expect(e.causes).toEqual([]);
  expect(e.passed).toContain("Балл БКИ 760 — не ниже порога одобрения 700");
  expect(e.help).toBe(
    "Серая зона: балл 760 выше порога 700 и ПДН 31% в пределах 60%, но ФССП не проверено — без этой проверки одобрить нельзя.",
  );
});

test("отказ по баллу: причина не дублируется в «Причине», ПДН — в норме", () => {
  const e = explainDecision({
    ...base,
    outcome: "reject",
    reason_code: "SCORE_BELOW_THRESHOLD",
    reason_text: "Скоринговый балл 420 ниже порога автоотказа 450.",
    score: 420,
  });
  expect(e.reason).toBe("Скоринговый балл 420 ниже порога автоотказа 450.");
  expect(e.causes).toEqual([]);
  expect(e.passed).toEqual(["ПДН 31% — в пределах порога 60%"]);
  expect(e.help).toBe("Отказ: ПДН 31% в пределах 60%, но балл 420 ниже порога отказа 450 — это ведёт к отказу.");
});

test("отказ по стоп-фактору: проваленное сравнение, которое не причина, — в «Причине»", () => {
  const e = explainDecision({
    ...base,
    outcome: "reject",
    reason_code: "FSSP_CREDIT_DEBT",
    triggered_rules: [{ id: "fssp_credit_debt" }],
    triggered_rules_text: ["Долг по исполнительному производству кредитного характера 60000 ₽ выше допустимых 50000 ₽."],
    pti: 0.7,
  });
  expect(e.reason).toContain("Долг по исполнительному производству");
  expect(e.causes).toEqual(["ПДН 70% — выше порога 60%"]);
  expect(e.passed).toEqual(["Балл БКИ 780 — не ниже порога одобрения 700"]);
});

test("ПДН: нет дохода — «не вычислимо», а не 0%; 0% при доходе — объяснён", () => {
  const noIncome = explainDecision({ ...base, pti: null, pti_status: "no_income" });
  expect(noIncome.unknown).toEqual(["ПДН: не вычислимо (нет данных о доходе)"]);
  expect([...noIncome.passed, ...noIncome.causes].join(" ")).not.toContain("ПДН");

  const noLoans = explainDecision({ ...base, pti: 0, bki_active_contracts_count: 0 });
  expect(noLoans.passed).toContain("ПДН 0% — действующих кредитов в КИ нет (порог 60%)");
  expect(noLoans.unknown).toEqual([]);

  const noBki = explainDecision({ ...base, pti: null, pti_status: "no_credit_history" });
  expect(noBki.unknown).toEqual(["ПДН: не вычислимо (кредитная история не получена)"]);
});

test("карточка серой зоны: «В норме» отдельно, одна «?» с объяснением этого решения", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.risk_manager.email, seed.risk_manager.password);
  await page.goto(`/applications/${seed.gray_fssp.id}`);

  await expect(page.getByRole("heading", { level: 1, name: /Серая зона/ })).toBeVisible();
  await expect(page.getByText("Проверка ФССП не пройдена").first()).toBeVisible();
  await expect(page.getByText(/Сработавшие/)).toHaveCount(0);
  await expect(page.getByTestId("decision-causes")).toHaveCount(0);
  const passed = page.getByTestId("decision-passed");
  await expect(passed).toContainText("Балл БКИ 760 — не ниже порога одобрения 700");
  await expect(passed).toContainText("ПДН 42% — в пределах порога 60%");

  // «Нужно сделать» — одна строка с кнопкой сразу под вердиктом.
  await expect(page.locator(".next-step")).toHaveCount(1);
  await expect(page.locator(".next-step").getByRole("link", { name: "К проверке ФССП" })).toBeVisible();

  // Подсказка «?» ровно одна, по клику — объяснение этого решения.
  const toggles = page.locator(".help-toggle");
  await expect(toggles).toHaveCount(1);
  await toggles.click();
  await expect(page.getByRole("note")).toHaveText(
    "Серая зона: балл 760 выше порога 700 и ПДН 42% в пределах 60%, но ФССП не проверено — без этой проверки одобрить нельзя.",
  );
});

test("карточка без дохода: «ПДН: не вычислимо», нигде нет «ПДН 0%»", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.risk_manager.email, seed.risk_manager.password);
  await page.goto(`/applications/${seed.no_income.id}`);

  await expect(page.getByTestId("decision-unknown")).toHaveText("ПДН: не вычислимо (нет данных о доходе)");
  await expect(page.getByText("ПДН 0%")).toHaveCount(0);
});
