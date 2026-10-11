// T20: объяснение решения в карточке — одна функция, чтобы вердикт, блок
// «Почему» и подсказка «?» у вердикта говорили одно и то же, без повторов:
//   reason  — причина одной строкой (только в вердикте);
//   causes  — «Причина»: детали, которых нет в строке вердикта;
//   passed  — «В норме»: пройденные сравнения с порогами;
//   unknown — что посчитать не удалось («не вычислимо»), с причиной;
//   help    — текст «?» у вердикта: объяснение ИМЕННО этого решения.
// Пороги — только из ответа API (версия правил этого решения), чисел из
// конфига здесь нет; здесь только арифметика сравнения и формулировки.

import { formatAmount } from "./format";
import { decisionOutcomeLabel, reasonCodeLabel } from "./labels";

export interface DecisionForExplain {
  stage: string;
  outcome: string;
  reason_code: string | null;
  reason_text: string | null;
  triggered_rules: { id?: string; field?: string; expected?: unknown; action?: string | null }[];
  triggered_rules_text: string[];
  score: string | number | null;
  score_auto_approve_above: number | null;
  score_auto_reject_below: number | null;
  pti: number | null;
  pti_max: number | null;
  pti_status: "computed" | "no_income" | "no_installment" | "no_credit_history" | null;
  bki_active_contracts_count: number | null;
  // T21: платёж по нашей рассрочке, вошедший в ПДН, и откуда он.
  installment_payment?: string | null;
  installment_payment_source?: "partner" | "tariff" | null;
}

export interface Explanation {
  reason: string | null;
  causes: string[];
  passed: string[];
  unknown: string[];
  help: string;
}

type Verdict = "pass" | "fail" | "middle";

interface Comparison {
  verdict: Verdict;
  text: string; // строка списка «Причина»/«В норме»
  phrase: string; // то же коротко — для подсказки у вердикта
  // T21: деталь, которой нет в строке вердикта (сумма платежа по рассрочке
  // в ПДН) — показывается в «Причине», даже когда само сравнение — причина.
  detail?: string;
  // Сравнение — это и есть причина решения (тот же смысл уже в строке
  // вердикта): в «Причине» второй раз не показываем.
  isDecisionReason: boolean;
}

const SCORE_REASON_CODES = new Set(["SCORE_BELOW_THRESHOLD", "SCORE_GRAY_ZONE"]);

function whole(value: number | string): string {
  return String(Math.round(Number(value)));
}

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function scoreComparison(d: DecisionForExplain): Comparison | null {
  if (d.score == null || d.score_auto_approve_above == null || d.score_auto_reject_below == null) return null;
  const score = Number(d.score);
  const approve = d.score_auto_approve_above;
  const reject = d.score_auto_reject_below;
  const isDecisionReason = d.reason_code != null && SCORE_REASON_CODES.has(d.reason_code);
  if (score >= approve) {
    return {
      verdict: "pass", isDecisionReason,
      text: `Балл БКИ ${whole(score)} — не ниже порога одобрения ${whole(approve)}`,
      phrase: score > approve
        ? `балл ${whole(score)} выше порога ${whole(approve)}`
        : `балл ${whole(score)} на пороге одобрения ${whole(approve)}`,
    };
  }
  if (score < reject) {
    return {
      verdict: "fail", isDecisionReason,
      text: `Балл БКИ ${whole(score)} — ниже порога отказа ${whole(reject)}`,
      phrase: `балл ${whole(score)} ниже порога отказа ${whole(reject)}`,
    };
  }
  return {
    verdict: "middle", isDecisionReason,
    text: `Балл БКИ ${whole(score)} — между порогами ${whole(reject)} и ${whole(approve)}`,
    phrase: `балл ${whole(score)} между порогами ${whole(reject)} и ${whole(approve)}`,
  };
}

function ptiComparison(d: DecisionForExplain): Comparison | null {
  if (d.pti_status !== "computed" || d.pti == null || d.pti_max == null) return null;
  // Сработало правило по ПДН (например, ручная проверка выше 55%) — его порог
  // и есть порог ЭТОГО решения; pti_max (отказ) здесь был бы не тем числом.
  const ptiRule = d.triggered_rules.find((rule) => rule.field === "features.pti" && typeof rule.expected === "number");
  const isDecisionReason = d.reason_code === "PTI_HIGH" || d.reason_code === "PTI_TOO_HIGH" || ptiRule != null;
  const threshold = ptiRule ? (ptiRule.expected as number) : d.pti_max;
  // T22: точно, какой это порог — отказа (limits.pti_max, после T22 — и для
  // ПДН по КИ) или ручной проверки (правило pti_high).
  const thresholdLabel =
    ptiRule && ptiRule.action !== "reject"
      ? `порога ручной проверки ${percent(threshold)}`
      : `порога отказа ${percent(threshold)}`;
  // T21: ПДН включает платёж по нашей рассрочке — так и пишем, с суммой;
  // если он посчитан по тарифу из правил, а не пришёл от заказчика, — помечаем.
  // T22: откуда платёж (по тарифу / от заказчика) — в строке «Платёж в месяц»
  // блока «Сделка», здесь не повторяем.
  const installmentNote =
    d.installment_payment != null ? `с учётом платежа по рассрочке ${formatAmount(d.installment_payment)} ₽` : "";
  const installment = installmentNote ? ` (${installmentNote})` : "";
  if (ptiRule != null || Math.round(d.pti * 100) > Math.round(d.pti_max * 100)) {
    return {
      verdict: "fail", isDecisionReason,
      text: `ПДН ${percent(d.pti)}${installment} — выше ${thresholdLabel}`,
      phrase: `ПДН ${percent(d.pti)} выше ${thresholdLabel}`,
      detail: installmentNote ? `ПДН ${percent(d.pti)} — ${installmentNote}` : undefined,
    };
  }
  // ПДН 0% — честный результат, а не «нет данных»: доход есть, действующих
  // кредитов в КИ нет (до T21 — ПДН без рассрочки, старые решения).
  const noLoans = d.pti === 0 && d.bki_active_contracts_count === 0;
  return {
    verdict: "pass", isDecisionReason,
    text: noLoans
      ? `ПДН 0% — действующих кредитов в КИ нет (порог отказа ${percent(d.pti_max)})`
      : installment
        ? `ПДН ${percent(d.pti)}${installment} — порог отказа ${percent(d.pti_max)}`
        : `ПДН ${percent(d.pti)} — в пределах порога отказа ${percent(d.pti_max)}`,
    phrase: noLoans ? "ПДН 0% (действующих кредитов нет)" : `ПДН ${percent(d.pti)} в пределах ${percent(d.pti_max)}`,
  };
}

function ptiUnknown(d: DecisionForExplain): string | null {
  if (d.pti_status === "no_income") return "ПДН: не вычислимо (нет данных о доходе)";
  if (d.pti_status === "no_installment") {
    return "ПДН: не вычислимо (нет платежа по рассрочке — заказчик не прислал, у тарифа нет наценки в правилах)";
  }
  if (d.pti_status === "no_credit_history") return "ПДН: не вычислимо (кредитная история не получена)";
  return null;
}

// «ФССП не проверено» → в середину фразы с маленькой буквы, но аббревиатуры
// (ФССП, БКИ, ПДН) оставляем как есть.
function inSentence(text: string): string {
  const trimmed = text.replace(/[.\s]+$/, "");
  if (trimmed.length > 1 && trimmed[1] === trimmed[1]!.toLowerCase()) {
    return trimmed[0]!.toLowerCase() + trimmed.slice(1);
  }
  return trimmed;
}

// Последствие причины — что из неё следует для этого решения.
function consequence(d: DecisionForExplain): string {
  if (d.reason_code === "FSSP_NOT_CHECKED") return "без этой проверки одобрить нельзя";
  if (d.reason_code === "BKI_NOT_CONFIGURED" || d.reason_code === "BKI_CONSENT_MISSING_OR_EXPIRED") {
    return "без кредитной истории одобрить нельзя";
  }
  if (d.outcome === "reject") return "это ведёт к отказу";
  if (d.outcome === "manual_review") return "автоматически решить нельзя — решает человек";
  return "";
}

function joinPhrases(phrases: string[]): string {
  if (phrases.length <= 1) return phrases.join("");
  return `${phrases.slice(0, -1).join(", ")} и ${phrases[phrases.length - 1]}`;
}

export function explainDecision(d: DecisionForExplain): Explanation {
  const label = decisionOutcomeLabel(d.stage, d.outcome);
  const comparisons = [scoreComparison(d), ptiComparison(d)].filter((c): c is Comparison => c !== null);

  // Строка вердикта: первое сработавшее правило, иначе причина по коду
  // (повторная заморозка по перечню и т.п. пишут правила пустыми).
  const reason =
    d.triggered_rules_text[0] ??
    d.reason_text ??
    (d.stage === "A" && d.outcome === "manual_review"
      ? "Стоп-факторов нет — заявка передана оператору для звонка."
      : null);

  const causes = [
    ...d.triggered_rules_text.slice(1),
    ...comparisons
      .filter((c) => !c.isDecisionReason && (c.verdict === "fail" || (c.verdict === "middle" && d.outcome === "manual_review")))
      .map((c) => c.text),
    // Сравнение — сама причина (уже в вердикте), но в вердикте нет суммы
    // платежа по рассрочке — её показываем деталью.
    ...comparisons.filter((c) => c.isDecisionReason && c.detail).map((c) => c.detail!),
  ];
  const passed = comparisons
    .filter((c) => c.verdict === "pass" || (c.verdict === "middle" && d.outcome !== "manual_review" && !c.isDecisionReason))
    .map((c) => c.text);
  const unknown = [ptiUnknown(d)].filter((line): line is string => line !== null);

  const passedPhrases = comparisons.filter((c) => c.verdict === "pass").map((c) => c.phrase);
  let help: string;
  if (d.outcome === "approve") {
    help = passedPhrases.length > 0
      ? `${label}: ${joinPhrases(passedPhrases)}, стоп-факторов нет.`
      : `${label}: стоп-факторов нет, все обязательные проверки пройдены.`;
  } else if (d.stage === "A" && d.outcome === "manual_review") {
    help = `${label}: на стадии А одобрения не бывает. Стоп-факторов не нашлось — оператор звонит клиенту и запускает стадию Б, там запрос в БКИ и итоговое решение.`;
  } else {
    const causeComparison = comparisons.find((c) => c.isDecisionReason);
    const cause = causeComparison
      ? causeComparison.phrase
      : d.reason_code
        ? inSentence(reasonCodeLabel(d.reason_code))
        : reason
          ? inSentence(reason)
          : "сработал стоп-фактор";
    const then = consequence(d);
    const otherPassed = comparisons.filter((c) => c.verdict === "pass" && !c.isDecisionReason).map((c) => c.phrase);
    const lead = otherPassed.length > 0 ? `${joinPhrases(otherPassed)}, но ${cause}` : cause;
    help = `${label}: ${lead}${then ? ` — ${then}` : ""}.`;
  }

  return { reason, causes, passed, unknown, help };
}
