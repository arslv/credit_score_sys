// T20: блок «Почему такое решение» — «Причина» (что реально привело к
// решению) отдельно от «В норме» (пройденные сравнения). Баг был: пройденные
// проверки (балл выше порога, ПДН в норме) шли списком «Сработавшие
// стоп-факторы» с красными точками. Плюс ПДН: «не вычислимо», а не 0%, когда
// дохода нет; одна подсказка «?» на экран — у вердикта, объясняет это решение.
import { test, expect } from "@playwright/test";
import { explainDecision, type DecisionForExplain } from "../src/lib/explain";
import { formatAmount } from "../src/lib/format";
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
    "ПДН 31% — в пределах порога отказа 60%",
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
  expect(e.passed).toEqual(["ПДН 31% — в пределах порога отказа 60%"]);
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
  expect(e.causes).toEqual(["ПДН 70% — выше порога отказа 60%"]);
  expect(e.passed).toEqual(["Балл БКИ 780 — не ниже порога одобрения 700"]);
});

test("ПДН с рассрочкой (T21): сумма платежа в строке ПДН, источник — только в «Сделке»", () => {
  const fromPartner = explainDecision({ ...base, pti: 0.48, installment_payment: "6500.00", installment_payment_source: "partner" });
  expect(fromPartner.passed).toContain(`ПДН 48% (с учётом платежа по рассрочке ${formatAmount("6500")} ₽) — порог отказа 60%`);

  const fromTariff = explainDecision({ ...base, pti: 0.48, installment_payment: "4500.00", installment_payment_source: "tariff" });
  // Источник платежа — только в «Сделке» (T22, без повтора в строке ПДН).
  expect(fromTariff.passed).toContain(`ПДН 48% (с учётом платежа по рассрочке ${formatAmount("4500")} ₽) — порог отказа 60%`);

  const over = explainDecision({
    ...base, outcome: "manual_review", reason_code: "PTI_HIGH", pti: 0.65,
    installment_payment: "6500.00", installment_payment_source: "partner",
  });
  expect(over.help).toContain("ПДН 65% выше порога отказа 60%");

  // Сработало правило ручной проверки по ПДН (55%) — порог в подсказке его,
  // не порог отказа (60%); сумма платежа — деталью в «Причине», раз в строке
  // вердикта её нет.
  const softRule = explainDecision({
    ...base, outcome: "manual_review", reason_code: "PTI_HIGH", pti: 0.83,
    triggered_rules: [{ id: "pti_high", field: "features.pti", expected: 0.55 }],
    triggered_rules_text: ["Долговая нагрузка 83% при допустимых 55%."],
    installment_payment: "4500.00", installment_payment_source: "partner",
  });
  expect(softRule.reason).toBe("Долговая нагрузка 83% при допустимых 55%.");
  expect(softRule.help).toBe(
    "Серая зона: балл 780 выше порога 700, но ПДН 83% выше порога ручной проверки 55% — автоматически решить нельзя — решает человек.",
  );
  expect(softRule.causes).toEqual([`ПДН 83% — с учётом платежа по рассрочке ${formatAmount("4500")} ₽`]);

  // T22: ПДН по КИ 83% выше порога отказа (limits.pti_max = 60%) — отказ;
  // порог в подсказке и детали — отказа, не ручной проверки.
  const reject = explainDecision({
    ...base, outcome: "reject", reason_code: "PTI_TOO_HIGH", pti: 0.83,
    triggered_rules: [{ id: "pti_max", field: "features.pti", expected: 0.6, action: "reject" }],
    triggered_rules_text: ["Долговая нагрузка по кредитной истории 83% выше порога отказа 60%."],
    installment_payment: "4500.00", installment_payment_source: "partner",
  });
  expect(reject.reason).toBe("Долговая нагрузка по кредитной истории 83% выше порога отказа 60%.");
  expect(reject.help).toBe("Отказ: балл 780 выше порога 700, но ПДН 83% выше порога отказа 60% — это ведёт к отказу.");
  expect(reject.causes).toEqual([`ПДН 83% — с учётом платежа по рассрочке ${formatAmount("4500")} ₽`]);

  const unknown = explainDecision({ ...base, pti: null, pti_status: "no_installment" });
  expect(unknown.unknown[0]).toMatch(/^ПДН: не вычислимо \(нет платежа по рассрочке/);
});

test("ПДН: нет дохода — «не вычислимо», а не 0%; 0% при доходе — объяснён", () => {
  const noIncome = explainDecision({ ...base, pti: null, pti_status: "no_income" });
  expect(noIncome.unknown).toEqual(["ПДН: не вычислимо (нет данных о доходе)"]);
  expect([...noIncome.passed, ...noIncome.causes].join(" ")).not.toContain("ПДН");

  const noLoans = explainDecision({ ...base, pti: 0, bki_active_contracts_count: 0 });
  expect(noLoans.passed).toContain("ПДН 0% — действующих кредитов в КИ нет (порог отказа 60%)");
  expect(noLoans.unknown).toEqual([]);

  const noBki = explainDecision({ ...base, pti: null, pti_status: "no_credit_history" });
  expect(noBki.unknown).toEqual(["ПДН: не вычислимо (кредитная история не получена)"]);
});

test("карточка серой зоны: «В норме» отдельно, одна «?» с объяснением этого решения", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto(`/applications/${seed.gray_fssp.id}`);

  await expect(page.getByRole("heading", { level: 1, name: /Серая зона/ })).toBeVisible();
  await expect(page.getByText("Проверка ФССП не пройдена").first()).toBeVisible();
  await expect(page.getByText(/Сработавшие/)).toHaveCount(0);
  await expect(page.getByTestId("decision-causes")).toHaveCount(0);
  const passed = page.getByTestId("decision-passed");
  await expect(passed).toContainText("Балл БКИ 760 — не ниже порога одобрения 700");
  await expect(passed).toContainText(`ПДН 42% (с учётом платежа по рассрочке ${formatAmount("4500")} ₽) — порог отказа 60%`);
  await expect(passed).not.toContainText("рассчитан по тарифу");

  // T22: платёж по рассрочке — в «Сделке», с пометкой, что рассчитан по тарифу.
  await expect(page.locator(".kv-row", { hasText: "Платёж в месяц" })).toContainText(
    `${formatAmount("4500")} ₽ (рассчитан по тарифу, не из системы заказчика)`,
  );

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
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto(`/applications/${seed.no_income.id}`);

  await expect(page.getByTestId("decision-unknown")).toHaveText("ПДН: не вычислимо (нет данных о доходе)");
  await expect(page.getByText("ПДН 0%")).toHaveCount(0);
  // T22: без «Нужно решение…» после «Нужно сделать:».
  await expect(page.locator(".next-step")).toHaveText("Нужно сделать:решение оператора в системе заказчика");
  // Платежа в снимке нет — строки «Платёж в месяц» нет (не «0 ₽»).
  await expect(page.locator(".kv-row", { hasText: "Платёж в месяц" })).toHaveCount(0);
});

// T23: «К проверке ФССП» прокручивает к разделу и на ~2 с подсвечивает его.
test("«К проверке ФССП» прокручивает к разделу и подсвечивает его", async ({ page }) => {
  const seed = loadSeed();
  await login(page, seed.admin.email, seed.admin.password);
  await page.goto(`/applications/${seed.gray_fssp.id}`);
  await page.getByRole("link", { name: "К проверке ФССП" }).click();
  const fsspSection = page.locator("#fssp").locator("xpath=ancestor::section[1]");
  await expect(fsspSection).toHaveClass(/section--flash/);
  await expect(fsspSection).toBeInViewport();
  await expect(fsspSection).not.toHaveClass(/section--flash/, { timeout: 5_000 });
  await expect(page).toHaveURL(/#fssp$/);
});
