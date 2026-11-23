import { useState } from "react";
import { formatAmount, formatDate } from "../lib/format";

type Details = Record<string, unknown>;
type Tone = "ok" | "warn" | "bad" | "none";
type Row = { label: string; value: string; tone?: Tone };

// «3-4» → «3–4»; «100000.0-149999.99» → «100 000 – 149 999 ₽».
function range(value: unknown, money = false): string | null {
  if (value == null || value === "") return null;
  const text = String(value);
  const m = /^\s*(-?[\d.]+)\s*-\s*(-?[\d.]+)\s*$/.exec(text);
  if (!m) return money ? `${formatAmount(text)} ₽` : text;
  if (!money) return `${m[1]}–${m[2]}`;
  return `${formatAmount(Math.floor(Number(m[1])))} – ${formatAmount(Math.floor(Number(m[2])))} ₽`;
}

// Худшая просрочка: без просрочек — зелёным, до 30 дней — жёлтым, дольше — красным.
function delayTone(days: unknown): Tone {
  if (typeof days !== "number") return "none";
  if (days === 0) return "ok";
  return days < 30 ? "warn" : "bad";
}

const str = (v: unknown) => (typeof v === "string" && v ? v : null);
const list = (v: unknown) => (Array.isArray(v) ? (v as unknown[]).map(String) : []);

/**
 * Кредитная история — разворот в плитке: действующие и закрытые кредиты,
 * просрочки сейчас и за всю историю, плохие долги, обращения в банки.
 * Бюро («Кредитный рейтинг») даёт сводку, а не список кредитов по одному:
 * по закрытым — виды, по оплате — худшая просрочка за всю историю.
 */
export function CreditDetails({ details }: { details: Details }) {
  const [open, setOpen] = useState(false);
  const openTypes = list(details.open_types);
  const closedTypes = list(details.closed_types);
  const activeCount = range(details.active_count);
  const summary = [
    activeCount ? `действующих: ${activeCount}` : openTypes.length > 0 ? `действующих видов: ${openTypes.length}` : null,
    closedTypes.length > 0 ? `закрытых видов: ${closedTypes.length}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const inquiries = (details.inquiries ?? {}) as Record<string, unknown>;
  const activeRows = (
    [
      { label: "Кредитов", value: activeCount },
      { label: "Поручительств", value: range(details.guarantor_count) },
      { label: "Средний платёж", value: range(details.average_payment, true) },
      { label: "Общий лимит", value: range(details.active_limit, true) },
      { label: "Просрочка сейчас", value: str(details.current_delay), tone: delayTone(details.current_delay_days) },
      { label: "Сумма просрочки", value: range(details.overdue_sum, true) },
    ] as { label: string; value: string | null; tone?: Tone }[]
  ).filter((r): r is Row => Boolean(r.value));

  return (
    <div className="crd">
      <button type="button" className="btn-link crd__toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? "Скрыть кредиты" : `Показать кредиты${summary ? ` (${summary})` : ""}`}
      </button>
      {open && (
        <div className="crd__body">
          <section className="crd__group">
            <h4 className="crd__title">Действующие</h4>
            {openTypes.length > 0 ? (
              <ul className="crd__chips">
                {openTypes.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            ) : (
              <p className="crd__empty">нет</p>
            )}
            <Rows rows={activeRows} />
          </section>

          <section className="crd__group">
            <h4 className="crd__title">Закрытые и история оплат</h4>
            {closedTypes.length > 0 ? (
              <ul className="crd__chips crd__chips--closed">
                {closedTypes.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            ) : (
              <p className="crd__empty">закрытых нет</p>
            )}
            <Rows
              rows={[
                ...(str(details.history_delay)
                  ? [
                      {
                        label: "Худшая просрочка за всю историю",
                        value: str(details.history_delay)!,
                        tone: delayTone(details.history_delay_days),
                      },
                    ]
                  : []),
                {
                  label: "Плохие долги, передача коллекторам",
                  value: details.bad_debt ? "были" : "не было",
                  tone: details.bad_debt ? "bad" : "ok",
                },
                ...(str(details.last_repayment_date)
                  ? [{ label: "Последнее погашение по графику", value: formatDate(String(details.last_repayment_date)) }]
                  : []),
              ]}
            />
          </section>

          {Object.values(inquiries).some((v) => v != null) && (
            <section className="crd__group">
              <h4 className="crd__title">Обращения в банки за кредитом</h4>
              <Rows
                rows={[
                  { label: "За 7 дней", value: String(inquiries["7d"] ?? "—") },
                  { label: "За 14 дней", value: String(inquiries["14d"] ?? "—") },
                  { label: "За месяц", value: String(inquiries["1mo"] ?? "—") },
                  { label: "Всего", value: String(inquiries.all ?? "—") },
                ]}
              />
            </section>
          )}
          <p className="crd__note">По каждому кредиту отдельно бюро в этом отчёте сведений не даёт — только сводку.</p>
        </div>
      )}
    </div>
  );
}

function Rows({ rows }: { rows: Row[] }) {
  if (rows.length === 0) return null;
  return (
    <dl className="crd__rows">
      {rows.map((r) => (
        <div key={r.label} className="crd__row">
          <dt>{r.label}</dt>
          <dd className={r.tone ? `crd__val--${r.tone}` : undefined}>{r.value}</dd>
        </div>
      ))}
    </dl>
  );
}
