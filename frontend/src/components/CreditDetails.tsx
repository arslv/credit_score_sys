import { useState } from "react";
import { formatAmount } from "../lib/format";

type Details = Record<string, unknown>;
type Tone = "ok" | "warn" | "bad" | "none";

// Бюро отдаёт количества и суммы диапазонами («5-9», «1.0-9999.99»).
function bounds(value: unknown): [number, number] | null {
  const m = /^\s*(-?[\d.]+)\s*-\s*(-?[\d.]+)\s*$/.exec(String(value ?? ""));
  if (!m) return null;
  return [Math.floor(Number(m[1])), Math.floor(Number(m[2]))];
}

// «5-9» → «от 5 до 9»; «0-2» → «до 2»; «3-3» → «3»; «0-0» → null (нет).
function countText(value: unknown): string | null {
  const b = bounds(value);
  if (!b) return value == null || value === "" ? null : String(value);
  if (b[1] <= 0) return null;
  if (b[0] === b[1]) return String(b[0]);
  return b[0] <= 0 ? `до ${b[1]}` : `от ${b[0]} до ${b[1]}`;
}

// «1.0-9999.99» → «до 9 999 ₽»; «5000.0-9999.99» → «от 5 000 до 9 999 ₽».
function moneyText(value: unknown): string | null {
  const b = bounds(value);
  if (!b) return value == null || value === "" ? null : `${formatAmount(String(value))} ₽`;
  if (b[1] <= 0) return null;
  return b[0] <= 1 ? `до ${formatAmount(b[1])} ₽` : `от ${formatAmount(b[0])} до ${formatAmount(b[1])} ₽`;
}

// Просрочка: без — зелёным, до 30 дней — жёлтым, дольше — красным.
function delayTone(days: unknown): Tone {
  if (typeof days !== "number") return "none";
  if (days === 0) return "ok";
  return days < 30 ? "warn" : "bad";
}

// «Оплата без просрочек» → «без просрочек»; «Просрочка более 180 дней» → «просрочка более 180 дней».
function delayText(value: unknown): string | null {
  if (typeof value !== "string" || !value) return null;
  if (/без просроч/i.test(value)) return "без просрочек";
  return value[0]!.toLowerCase() + value.slice(1);
}

const list = (v: unknown) => (Array.isArray(v) ? (v as unknown[]).map(String) : []);
const lower = (items: string[]) => items.map((t, i) => (i === 0 ? t : t[0]!.toLowerCase() + t.slice(1))).join(", ");

/**
 * Кредитная история — разворот в плитке (по отзыву 23.11.2026 — понятными
 * фразами): сверху три главных сигнала цветом — просрочка сейчас, худшая за
 * всю историю, плохие долги; ниже — сколько кредитов и каких, сколько платит,
 * закрытые кредиты, обращения в банки. Бюро даёт сводку, а не список кредитов.
 */
export function CreditDetails({ details }: { details: Details }) {
  const [open, setOpen] = useState(false);
  const openTypes = list(details.open_types);
  const closedTypes = list(details.closed_types);
  const activeCount = countText(details.active_count);

  const nowDelay = delayText(details.current_delay);
  const nowSum = moneyText(details.overdue_sum);
  const historyDelay = delayText(details.history_delay);
  const signals: { text: string; tone: Tone }[] = [];
  if (nowDelay) {
    const tone = delayTone(details.current_delay_days);
    signals.push({ text: `Сейчас: ${nowDelay}${tone !== "ok" && nowSum ? `, на сумму ${nowSum}` : ""}`, tone });
  }
  if (historyDelay) signals.push({ text: `За всю историю: ${historyDelay}`, tone: delayTone(details.history_delay_days) });
  signals.push(
    details.bad_debt
      ? { text: "Были плохие долги или передача коллекторам", tone: "bad" }
      : { text: "Плохих долгов и коллекторов не было", tone: "ok" },
  );

  const facts: { label: string; value: string }[] = [];
  if (activeCount || openTypes.length > 0) {
    facts.push({
      label: "Сейчас кредитов",
      // После числа («от 3 до 4 — потребительский кредит») — со строчной.
      value: [activeCount, openTypes.length > 0 ? lower(activeCount ? ["", ...openTypes] : openTypes).replace(/^, /, "") : null]
        .filter(Boolean)
        .join(" — "),
    });
  } else {
    facts.push({ label: "Сейчас кредитов", value: "нет" });
  }
  const payment = moneyText(details.average_payment);
  if (payment) facts.push({ label: "Платит в месяц", value: payment });
  const limit = moneyText(details.active_limit);
  if (limit) facts.push({ label: "Общий лимит", value: limit });
  const guarantor = countText(details.guarantor_count);
  facts.push({ label: "Поручительств по чужим кредитам", value: guarantor ?? "нет" });
  facts.push({ label: "Закрытые кредиты", value: closedTypes.length > 0 ? lower(closedTypes) : "нет" });

  const inquiries = (details.inquiries ?? {}) as Record<string, unknown>;
  const inquiryParts = [
    typeof inquiries["14d"] === "number" && inquiries["14d"] > 0 ? `${inquiries["14d"]} за 2 недели` : null,
    typeof inquiries["1mo"] === "number" && inquiries["1mo"] > 0 ? `${inquiries["1mo"]} за месяц` : null,
    typeof inquiries.all === "number" ? `всего ${inquiries.all}` : null,
  ].filter(Boolean);
  if (inquiryParts.length > 0) facts.push({ label: "Обращался за кредитом", value: inquiryParts.join(" · ") });

  const summary = [
    activeCount ? `сейчас ${activeCount}` : null,
    closedTypes.length > 0 ? `закрытых видов ${closedTypes.length}` : null,
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <div className="crd">
      <button type="button" className="btn-link crd__toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? "Скрыть кредиты" : `Показать кредиты${summary ? ` (${summary})` : ""}`}
      </button>
      {open && (
        <div className="crd__body">
          <ul className="crd__signals">
            {signals.map((s) => (
              <li key={s.text} className={`crd__signal crd__signal--${s.tone}`}>
                {s.text}
              </li>
            ))}
          </ul>
          <dl className="crd__facts">
            {facts.map((f) => (
              <div key={f.label} className="crd__fact">
                <dt>{f.label}</dt>
                <dd>{f.value}</dd>
              </div>
            ))}
          </dl>
          <p className="crd__note">Бюро даёт сводку по кредитам, без списка по одному.</p>
        </div>
      )}
    </div>
  );
}
