import type { components } from "../api/schema";
import { checkResultLabel, passportCheckLabel, passportCheckStatusLabel, sourceMeta } from "../lib/labels";

type CheckRow = components["schemas"]["CheckOut"];
type BureauCheck = components["schemas"]["BureauCheckOut"];
type PassportCheck = components["schemas"]["PassportCheckOut"];

export type Tone = "ok" | "bad" | "warn" | "none";

const TONE_ICON: Record<Tone, string> = { ok: "✓", bad: "✕", warn: "!", none: "–" };
const TONE_LABEL: Record<Tone, string> = { ok: "в порядке", bad: "нарушение", warn: "замечание", none: "нет данных" };

type Row = { key: string; name: string; value: string; tone: Tone; bureau?: boolean; who?: string; hint?: string };

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
// Ручные проверки: пока их нет — строка «не проверено», а не пропуск.
const MANUAL_SOURCES = ["fssp", "fedresurs"];
// Проверки бюро по своим разделам; паспорт из бюро — в «Паспорт».
const BUREAU_GROUP: Record<string, string> = {
  mvd: "Паспорт",
  rosfinmon: "Перечни",
  fns_inn: "Налоговая",
  fns_npd: "Налоговая",
  fssp: "Долги и банкротство",
  fedresurs: "Долги и банкротство",
};

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

// Последняя по времени проверка каждой пары «источник + субъект».
function latest<T extends { source: string; subject_type: string; subject_id?: string | null }>(rows: T[]): T[] {
  const seen = new Map<string, T>();
  for (const row of rows) seen.set(`${row.source}|${row.subject_type}|${row.subject_id ?? ""}`, row);
  return [...seen.values()];
}

/**
 * «Проверки» — всё, что проверено по заявке, на одном экране: паспорт,
 * перечни, налоговая, долги и банкротство, кредитная история; наши проверки
 * и проверки из отчёта бюро (пометка «БКИ»). Подробности и история повторов —
 * в «Данные заявки → Что проверили».
 */
export function ChecksOverview({
  passport,
  checks,
  bureau,
  guarantorId,
  bki,
}: {
  passport: PassportCheck[];
  checks: CheckRow[];
  bureau: BureauCheck[];
  guarantorId: string | null;
  bki: { value: string; tone: Tone } | null;
}) {
  // МВД (отменённый реестр) не показываем — паспорт ниже по пунктам.
  const current = latest(
    checks.filter(
      (c) => c.source !== "mvd" && (c.subject_type !== "guarantor" || (c.subject_id ?? null) === guarantorId),
    ),
  );
  const bureauLatest = latest(bureau.map((b) => ({ ...b, subject_id: null })));

  const rowOf = (c: CheckRow): Row => ({
    key: c.id,
    name: sourceMeta(c.source).label,
    value: checkResultLabel(c.source, c.status),
    tone: checkTone(c.status),
    who: c.subject_type === "guarantor" ? "поручитель" : undefined,
  });
  const bureauRow = (b: BureauCheck): Row => {
    const summary = typeof b.parsed?.summary === "string" ? b.parsed.summary : null;
    return {
      key: `bureau-${b.id}`,
      name: b.source === "mvd" ? "Реестр недействительных" : sourceMeta(b.source).label,
      value: summary ?? checkResultLabel(b.source, b.status),
      tone: checkTone(b.status),
      bureau: true,
    };
  };

  const sections: { title: string; rows: Row[] }[] = [];

  const passportRows: Row[] = passport.map((p) => ({
    key: `passport-${p.key}`,
    name: PASSPORT_SHORT[p.key] ?? passportCheckLabel(p.key),
    hint: passportCheckLabel(p.key),
    value: passportCheckStatusLabel(p.status),
    tone: passportTone(p.status),
  }));
  passportRows.push(...bureauLatest.filter((b) => BUREAU_GROUP[b.source] === "Паспорт").map(bureauRow));
  sections.push({ title: "Паспорт", rows: passportRows });

  for (const group of GROUPS) {
    const rows = current.filter((c) => group.sources.includes(c.source)).map(rowOf);
    for (const source of MANUAL_SOURCES) {
      if (group.sources.includes(source) && !current.some((c) => c.source === source && c.subject_type === "applicant")) {
        rows.push({ key: `pending-${source}`, name: sourceMeta(source).label, value: "не проверено", tone: "none" });
      }
    }
    rows.push(...bureauLatest.filter((b) => BUREAU_GROUP[b.source] === group.title).map(bureauRow));
    sections.push({ title: group.title, rows });
  }

  sections.push({
    title: "Кредитная история",
    rows: [{ key: "bki", name: "Отчёт бюро", value: bki?.value ?? "не запрашивался", tone: bki?.tone ?? "none" }],
  });

  const known = new Set([...GROUPS.flatMap((g) => g.sources), "mvd"]);
  const other = current.filter((c) => !known.has(c.source)).map(rowOf);
  if (other.length > 0) sections.push({ title: "Прочее", rows: other });

  const all = sections.flatMap((s) => s.rows);
  const count = (tone: Tone) => all.filter((r) => r.tone === tone).length;

  return (
    <section className="pf-card chk" aria-labelledby="chk-title">
      <header className="chk__head">
        <h2 id="chk-title" className="pf-section-title">
          Проверки
        </h2>
        <ul className="chk__summary" aria-label="Итог проверок">
          {(["ok", "warn", "bad", "none"] as Tone[]).filter((tone) => count(tone) > 0).map((tone) => (
            <li key={tone} className={`chk__count chk__count--${tone}`} title={TONE_LABEL[tone]}>
              <span className="chk__icon" aria-hidden="true">
                {TONE_ICON[tone]}
              </span>
              {count(tone)}
              <span className="sr-only"> — {TONE_LABEL[tone]}</span>
            </li>
          ))}
        </ul>
      </header>

      <div className="chk__grid">
        {sections.map((section) => (
          <section key={section.title} className="chk__group" aria-label={section.title}>
            <h3 className="chk__group-title">{section.title}</h3>
            {section.rows.length === 0 ? (
              <p className="chk__empty">ещё не проверялось</p>
            ) : (
              <ul className="chk__rows">
                {section.rows.map((row) => (
                  <li key={row.key} className={`chk__row chk__row--${row.tone}`}>
                    <span className="chk__icon" aria-hidden="true">
                      {TONE_ICON[row.tone]}
                    </span>
                    <span className="chk__name" title={row.hint}>
                      {row.name}
                      {row.who && <span className="chk__who"> · {row.who}</span>}
                      {row.bureau && (
                        <span className="bki-tag" title="Проверка из отчёта бюро кредитных историй">
                          БКИ
                        </span>
                      )}
                    </span>
                    <span className="chk__value">{row.value}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        ))}
      </div>
    </section>
  );
}
