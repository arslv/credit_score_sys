// T20: объяснение решения в карточке — одна функция, чтобы вердикт, блок
// «Почему» и подсказка «?» у вердикта говорили одно и то же, без повторов:
//   reason  — причина одной строкой (только в вердикте);
//   causes  — «Причина»: детали, которых нет в строке вердикта;
//   passed  — «В норме»: пройденные сравнения с порогами;
//   unknown — что посчитать не удалось («не вычислимо»), с причиной;
//   help    — текст «?» у вердикта: объяснение ИМЕННО этого решения.
// Пороги — только из ответа API (версия правил этого решения), чисел из
// конфига здесь нет; здесь только арифметика сравнения и формулировки.

import { decisionOutcomeLabel, reasonCodeLabel } from "./labels";

export interface DecisionForExplain {
  stage: string;
  outcome: string;
  reason_code: string | null;
  reason_text: string | null;
  triggered_rules: { id?: string }[];
  triggered_rules_text: string[];
  score: string | number | null;
  score_auto_approve_above: number | null;
  score_auto_reject_below: number | null;
  pti: number | null;
  pti_max: number | null;
  pti_status: "computed" | "no_income" | "no_credit_history" | null;
  bki_active_contracts_count: number | null;
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
  const isDecisionReason =
    d.reason_code === "PTI_HIGH" || d.triggered_rules.some((rule) => rule.id === "pti_high");
  if (Math.round(d.pti * 100) > Math.round(d.pti_max * 100)) {
    return {
      verdict: "fail", isDecisionReason,
      text: `ПДН ${percent(d.pti)} — выше порога ${percent(d.pti_max)}`,
      phrase: `ПДН ${percent(d.pti)} выше порога ${percent(d.pti_max)}`,
    };
  }
  // ПДН 0% — честный результат, а не «нет данных»: доход есть, действующих
  // кредитов в КИ нет. Так и пишем, чтобы ноль не выглядел ошибкой.
  const noLoans = d.pti === 0 && d.bki_active_contracts_count === 0;
  return {
    verdict: "pass", isDecisionReason,
    text: noLoans
      ? `ПДН 0% — действующих кредитов в КИ нет (порог ${percent(d.pti_max)})`
      : `ПДН ${percent(d.pti)} — в пределах порога ${percent(d.pti_max)}`,
    phrase: noLoans ? "ПДН 0% (действующих кредитов нет)" : `ПДН ${percent(d.pti)} в пределах ${percent(d.pti_max)}`,
  };
}

function ptiUnknown(d: DecisionForExplain): string | null {
  if (d.pti_status === "no_income") return "ПДН: не вычислимо (нет данных о доходе)";
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
