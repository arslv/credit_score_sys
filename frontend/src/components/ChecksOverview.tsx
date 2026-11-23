import type { ReactNode } from "react";
import type { components } from "../api/schema";
import { formatAmount, formatDateTime } from "../lib/format";
import { CreditDetails } from "./CreditDetails";
import { BureauFsspDetails, FsspDetails } from "./FsspDetails";
import { checkResultLabel, fsspEndReason, passportCheckLabel, passportCheckStatusLabel, sourceMeta } from "../lib/labels";

type CheckRow = components["schemas"]["CheckOut"];
type BureauCheck = components["schemas"]["BureauCheckOut"];
type PassportCheck = components["schemas"]["PassportCheckOut"];
type BkiResult = components["schemas"]["BkiResultOut"];

export type Tone = "ok" | "bad" | "warn" | "none";

// Итог одной находки: что показал источник; bureau — из отчёта БКИ.
type Line = { value: string; tone: Tone; bureau?: boolean; details?: ReactNode };
// Строка — одна проверка (источник + субъект). Наша проверка и та же
// проверка из отчёта бюро — в одной строке двумя итогами, а не двумя строками.
type Row = { key: string; name: string; who?: string; hint?: string; lines: Line[]; quiet?: boolean };

// Худший итог строки: нарушение важнее замечания, «в порядке» — важнее «нет
// данных» (ИНН не нашли мы, но нашло бюро — значит, ИНН есть).
const TONE_RANK: Record<Tone, number> = { bad: 3, warn: 2, ok: 1, none: 0 };
const worst = (tones: Tone[]): Tone =>
  tones.reduce<Tone>((acc, t) => (TONE_RANK[t] > TONE_RANK[acc] ? t : acc), "none");

function plural(n: number, forms: [string, string, string]): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return forms[0];
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return forms[1];
  return forms[2];
}

// Место раздела в сетке «Проверок» (grid-template-areas в index.css).
const AREA: Record<string, string> = {
  Паспорт: "passport",
  Перечни: "lists",
  Налоговая: "tax",
  "Долги и банкротство": "debts",
};

const TONE_TEXT: Record<Tone, (n: number) => string> = {
  ok: () => "в порядке",
  bad: (n) => plural(n, ["нарушение", "нарушения", "нарушений"]),
  warn: (n) => plural(n, ["замечание", "замечания", "замечаний"]),
  none: () => "нет данных",
};

// Короткие названия проверок паспорта для этого блока; полные — в подсказке.
const PASSPORT_SHORT: Record<string, string> = {
  format: "Формат серии и номера",
  expiry: "Срок действия",
  issue_date: "Дата выдачи",
  blank_year: "Год бланка",
  department_code: "Код подразделения",
  reuse: "Не встречался с другими ФИО",
};

// Группы — в порядке, в котором по заявке принимают решение.
const GROUPS: { title: string; sources: string[] }[] = [
  { title: "Перечни", sources: ["rosfinmon", "blacklist"] },
  { title: "Налоговая", sources: ["fns_inn", "fns_npd", "fns_egrip", "fns"] },
  { title: "Долги и банкротство", sources: ["fssp", "fedresurs"] },
];
// Ручные проверки: пока их нет — итог «не проверено», а не пропуск.
const MANUAL_SOURCES = ["fssp", "fedresurs"];
// Проверки бюро по своим разделам; паспорт из бюро — в «Паспорт».
const BUREAU_GROUP: Record<string, string> = {
  mvd: "Паспорт",
  rosfinmon: "Перечни",
  fns_inn: "Налоговая",
  fns_npd: "Налоговая",
  fssp: "Долги и банкротство",
  fedresurs: "Долги и банкротство",
  // Только из отчёта бюро (23.11.2026): реестры — к перечням, суды и залоги — к долгам.
  wanted: "Перечни",
  fsin_wanted: "Перечни",
  inoagent: "Перечни",
  disqualified: "Перечни",
  trust_loss: "Перечни",
  courts: "Долги и банкротство",
  pledge: "Долги и банкротство",
};

// Итог нашей проверки на сайте источника. У ФССП — сумма долга прямо в
// строке и производства разворотом под ней (как у строки «БКИ»), чтобы было
// видно, к какому источнику что относится (по отзыву 23.11.2026).
function siteLine(c: CheckRow): Line {
  const line = fsspEndedLine(c) ?? { value: checkResultLabel(c.source, c.status), tone: checkTone(c.status) };
  if (c.source !== "fssp") return line;
  const total = (c.parsed as { fssp_total_amount?: number } | null)?.fssp_total_amount;
  if (c.status === "hit" && typeof total === "number" && total > 0) {
    line.value = `${line.value} · ${formatAmount(total)} ₽`;
  }
  if (c.fssp_details && c.fssp_details.length > 0) line.details = <FsspDetails details={c.fssp_details} />;
  return line;
}

// ФССП: открытых производств нет, но оконченные (в т. ч. оплаченные) были —
// не «ничего не найдено» зелёным, а жёлтым с итогом (решение владельца 23.11.2026).
function fsspEndedLine(c: CheckRow): Line | null {
  if (c.source !== "fssp" || c.status !== "ok" || !c.fssp_details) return null;
  const ended = c.fssp_details.filter((d) => d.status !== "active");
  if (ended.length === 0) return null;
  const paid = ended.filter((d) => fsspEndReason(d.end_reason).paid).length;
  return {
    value: `открытых нет · оконченных ${ended.length}${paid > 0 ? `, оплачено ${paid}` : ""}`,
    tone: "warn",
  };
}

function checkTone(status: string): Tone {
  if (status === "ok") return "ok";
  if (status === "hit") return "bad";
  if (status === "source_unavailable" || status === "error") return "warn";
  return "none";
}

function passportTone(status: string): Tone {
  if (status === "ok") return "ok";
  if (status === "fail") return "bad";
  if (status === "warn") return "warn";
  return "none";
}

// Ключ строки «источник + субъект»; по нему же карточка заявки передаёт
// кнопки ручных проверок (ФССП, Федресурс) под нужную строку.
export const subjectKey = (source: string, subjectType: string, subjectId?: string | null) =>
  `${source}|${subjectType}|${subjectType === "guarantor" ? (subjectId ?? "") : ""}`;

// Последняя по времени проверка каждой пары «источник + субъект».
function latest<T extends { source: string; subject_type: string; subject_id?: string | null }>(rows: T[]): T[] {
  const seen = new Map<string, T>();
  for (const row of rows) seen.set(subjectKey(row.source, row.subject_type, row.subject_id), row);
  return [...seen.values()];
}

/**
 * «Проверки» — всё, что проверено по заявке, на одном экране: кредитная
 * история, паспорт, перечни, налоговая, долги и банкротство. Наша проверка и
 * та же проверка из отчёта бюро — одной строкой (итог бюро помечен «БКИ»).
 * Подробности и история повторов — в «Данные заявки → Что проверили».
 */
export function ChecksOverview({
  passport,
  checks,
  bureau,
  guarantorId,
  guarantorName = null,
  bki,
  bkiAction = null,
  rowActions = {},
}: {
  passport: PassportCheck[];
  checks: CheckRow[];
  bureau: BureauCheck[];
  guarantorId: string | null;
  /** ФИО поручителя — рядом с пометкой «поручитель», чтобы было видно, кого проверяем. */
  guarantorName?: string | null;
  bki: BkiResult | null;
  /** Кнопки запроса отчёта БКИ — внизу плитки «Кредитная история». */
  bkiAction?: ReactNode;
  /** Действия под строкой проверки, по subjectKey (ручные ФССП и Федресурс). */
  rowActions?: Record<string, ReactNode>;
}) {
  // МВД (отменённый реестр) не показываем — паспорт ниже по пунктам.
  const current = latest(
    checks.filter(
      (c) => c.source !== "mvd" && (c.subject_type !== "guarantor" || (c.subject_id ?? null) === guarantorId),
    ),
  );
  const bureauLatest = latest(bureau);
  const guarantorWho = guarantorName ? `поручитель · ${guarantorName}` : "поручитель";

  const bureauLine = (b: BureauCheck): Line => ({
    value: typeof b.parsed?.summary === "string" ? b.parsed.summary : checkResultLabel(b.source, b.status),
    tone: checkTone(b.status),
    bureau: true,
    // ФССП от бюро — производства с разворотом (23.11.2026).
    details: b.source === "fssp" && b.parsed ? <BureauFsspDetails parsed={b.parsed} /> : undefined,
  });

  // Строки раздела: наши проверки, «не проверено» для ручных и итоги бюро —
  // по ключу «источник + субъект», бюро добавляется к нашей строке.
  function buildRows(sources: string[], title: string): Row[] {
    const rows = new Map<string, Row>();
    for (const c of current.filter((x) => sources.includes(x.source))) {
      const key = subjectKey(c.source, c.subject_type, c.subject_id);
      rows.set(key, {
        key,
        name: sourceMeta(c.source).label,
        who: c.subject_type === "guarantor" ? guarantorWho : undefined,
        lines: [siteLine(c)],
      });
    }
    // Ручные проверки — у заявителя и поручителя (если он есть) всегда строкой.
    const manualSubjects: [string, string | null][] = [["applicant", null], ...(guarantorId ? [["guarantor", guarantorId] as [string, string]] : [])];
    for (const source of MANUAL_SOURCES) {
      for (const [subjectType, subjectId] of manualSubjects) {
        const key = subjectKey(source, subjectType, subjectId);
        if (sources.includes(source) && !rows.has(key)) {
          rows.set(key, {
            key,
            name: sourceMeta(source).label,
            who: subjectType === "guarantor" ? guarantorWho : undefined,
            lines: [{ value: "не проверено", tone: "none" }],
          });
        }
      }
    }
    for (const b of bureauLatest.filter((x) => BUREAU_GROUP[x.source] === title)) {
      const key = subjectKey(b.source, b.subject_type);
      const existing = rows.get(key);
      if (existing) existing.lines.push(bureauLine(b));
      else
        rows.set(key, {
          key,
          name: b.source === "mvd" ? "Реестр недействительных" : sourceMeta(b.source).label,
          lines: [bureauLine(b)],
        });
    }
    return [...rows.values()];
  }

  const sections: { title: string; rows: Row[] }[] = [];

  const passportRows: Row[] = passport.map((p) => ({
    key: `passport-${p.key}`,
    name: PASSPORT_SHORT[p.key] ?? passportCheckLabel(p.key),
    hint: passportCheckLabel(p.key),
    lines: [{ value: passportCheckStatusLabel(p.status), tone: passportTone(p.status) }],
    // «в порядке» у пункта паспорта ничего не добавляет к зелёной галочке.
    quiet: p.status === "ok",
  }));
  passportRows.push(...buildRows([], "Паспорт"));
  sections.push({ title: "Паспорт", rows: passportRows });

  for (const group of GROUPS) sections.push({ title: group.title, rows: buildRows(group.sources, group.title) });

  const known = new Set([...GROUPS.flatMap((g) => g.sources), "mvd"]);
  const other = current
    .filter((c) => !known.has(c.source))
    .map<Row>((c) => ({
      key: c.id,
      name: sourceMeta(c.source).label,
      who: c.subject_type === "guarantor" ? guarantorWho : undefined,
      lines: [{ value: checkResultLabel(c.source, c.status), tone: checkTone(c.status) }],
    }));
  if (other.length > 0) sections.push({ title: "Прочее", rows: other });

  const bkiTone: Tone = !bki ? "none" : bki.status === "ok" ? "ok" : bki.status === "no_data" ? "none" : "warn";
  const tones = [...sections.flatMap((s) => s.rows.map((r) => worst(r.lines.map((l) => l.tone)))), bkiTone];
  const count = (tone: Tone) => tones.filter((t) => t === tone).length;
  const order: Tone[] = ["ok", "bad", "warn", "none"];
  const present = order.filter((t) => count(t) > 0);
  const summaryText = present.map((t) => `${count(t)} ${TONE_TEXT[t](count(t))}`).join(", ");

  return (
    <section className="pf-card chk" aria-labelledby="chk-title">
      <header className="chk__head">
        <div className="chk__head-text">
          <h2 id="chk-title" className="pf-section-title">
            Проверки
          </h2>
          <p className="chk__total">
            {tones.length} {plural(tones.length, ["проверка", "проверки", "проверок"])}
          </p>
        </div>
        <div className="chk__summary">
          <div className="chk__bar" role="img" aria-label={`Итог проверок: ${summaryText}`}>
            {present.map((t) => (
              <span key={t} className={`chk__bar-seg chk__bar-seg--${t}`} style={{ flexGrow: count(t) }} />
            ))}
          </div>
          <ul className="chk__legend" aria-hidden="true">
            {present.map((t) => (
              <li key={t} className={`chk__legend-item chk__legend-item--${t}`}>
                <span className="chk__dot" />
                <b>{count(t)}</b> {TONE_TEXT[t](count(t))}
              </li>
            ))}
          </ul>
        </div>
      </header>

      <div className="chk__grid">
        <BkiTile result={bki} tone={bkiTone} action={bkiAction} />
        {sections.map((section) => (
          <GroupCard key={section.title} title={section.title} rows={section.rows} actions={rowActions} />
        ))}
      </div>
    </section>
  );
}

function GroupCard({ title, rows, actions }: { title: string; rows: Row[]; actions: Record<string, ReactNode> }) {
  const rowTones = rows.map((r) => worst(r.lines.map((l) => l.tone)));
  const bad = rowTones.filter((t) => t === "bad").length;
  const warn = rowTones.filter((t) => t === "warn").length;
  const ok = rowTones.filter((t) => t === "ok").length;
  const stat =
    bad > 0
      ? { tone: "bad" as Tone, text: `${bad} ${TONE_TEXT.bad(bad)}` }
      : warn > 0
        ? { tone: "warn" as Tone, text: `${warn} ${TONE_TEXT.warn(warn)}` }
        : { tone: ok === rows.length && rows.length > 0 ? ("ok" as Tone) : ("none" as Tone), text: `${ok} из ${rows.length}` };

  return (
    <section className={`chk__group chk__group--${stat.tone} chk__area--${AREA[title] ?? "other"}`} aria-label={title}>
      <header className="chk__group-head">
        <h3 className="chk__group-title">{title}</h3>
        {rows.length > 0 && <span className={`chk__group-stat chk__group-stat--${stat.tone}`}>{stat.text}</span>}
      </header>
      {rows.length === 0 ? (
        <p className="chk__empty">ещё не проверялось</p>
      ) : (
        <ul className="chk__rows">
          {rows.map((row, i) => {
            const tone = rowTones[i]!;
            return (
              <li key={row.key} className={`chk__row chk__row--${tone}`}>
                <StatusIcon tone={tone} />
                <div className="chk__body">
                  <div className="chk__name" title={row.hint}>
                    {row.name}
                    {row.who && <span className="chk__who">{row.who}</span>}
                  </div>
                  {!row.quiet &&
                    row.lines.map((line, j) => [
                      <div key={j} className={`chk__line chk__line--${line.tone}`}>
                        {line.bureau ? (
                          <span className="bki-tag" title="Проверка из отчёта бюро кредитных историй">
                            БКИ
                          </span>
                        ) : (
                          // Рядом с итогом бюро — подписать и наш: проверка на сайте источника.
                          row.lines.length > 1 && (
                            <span className="bki-tag chk__site-tag" title="Наша проверка на сайте источника">
                              сайт
                            </span>
                          )
                        )}
                        <span className="chk__value">{line.value}</span>
                      </div>,
                      line.details ? (
                        <div key={`${j}-details`} className="chk__line-details">
                          {line.details}
                        </div>
                      ) : null,
                    ])}
                  {actions[row.key] && <div className="chk__action">{actions[row.key]}</div>}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

// Значок статуса — мягкий круг цвета итога с рисованным знаком (а не буквой
// шрифта: одинаково чётко в любой теме и масштабе).
export function StatusIcon({ tone }: { tone: Tone }) {
  return (
    <span className={`chk__icon chk__icon--${tone}`} aria-hidden="true">
      <svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
        {tone === "ok" && <path d="M3.5 8.5l3 3 6-7" />}
        {tone === "bad" && <path d="M4.5 4.5l7 7M11.5 4.5l-7 7" />}
        {tone === "warn" && <path d="M8 3.5v5.5M8 12.5v0.01" />}
        {tone === "none" && <path d="M4.5 8h7" />}
      </svg>
    </span>
  );
}

// Кредитная история — плитка: крупный балл (или прочерк), итог, вид отчёта и дата.
function BkiTile({ result, tone, action }: { result: BkiResult | null; tone: Tone; action: ReactNode }) {
  const score = result?.status === "ok" && result.score != null ? String(Math.round(Number(result.score))) : null;
  const outcome = !result
    ? "Отчёт ещё не запрашивался"
    : result.status === "ok"
      ? score
        ? "Отчёт получен"
        : "Отчёт получен, балла нет"
      : result.status === "no_data"
        ? "Кредитной истории в бюро нет"
        : result.status === "source_unavailable"
          ? "Бюро не ответило"
          : "Ошибка запроса";
  return (
    <section className={`chk__group chk__bki chk__bki--${tone} chk__area--bki`} aria-label="Кредитная история">
      <header className="chk__group-head">
        <h3 className="chk__group-title">Кредитная история</h3>
        {result?.label && <span className="chk__bki-kind">{result.label}</span>}
      </header>
      {score && (
        <div className="chk__bki-score">
          {score}
          <span className="chk__bki-unit">балл</span>
        </div>
      )}
      <div className={`chk__bki-outcome${score ? "" : " chk__bki-outcome--plain"}`}>
        <StatusIcon tone={tone} />
        {outcome}
      </div>
      {result && <div className="chk__bki-meta">{formatDateTime(result.requested_at)}</div>}
      {/* Кредиты — действующие и закрытые, просрочки за историю (решение владельца 23.11.2026). */}
      {result?.credit_details && <CreditDetails details={result.credit_details as Record<string, unknown>} />}
      {action && <div className="chk__bki-action">{action}</div>}
    </section>
  );
}
