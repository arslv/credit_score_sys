import { useState } from "react";
import type { components } from "../api/schema";
import { formatAmount } from "../lib/format";
import { fsspEndReason } from "../lib/labels";

type Production = components["schemas"]["FsspProductionOut"];

const plural = (n: number, forms: [string, string, string]) => {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return forms[0];
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return forms[1];
  return forms[2];
};

/**
 * ФССП — производства по найденному: за что долг (предмет и сумма), номер и
 * дата производства, документ-основание, отдел; у оконченных — когда и по
 * какой статье окончено (оплачено, не нашли имущество и т. п.). Свёрнуто:
 * строка-итог, по клику — список.
 */
export function FsspDetails({ details }: { details: Production[] }) {
  const [open, setOpen] = useState(false);
  if (details.length === 0) return null;
  const active = details.filter((d) => d.status === "active");
  const ended = details.filter((d) => d.status !== "active");
  const paid = ended.filter((d) => fsspEndReason(d.end_reason).paid).length;
  const summary = [
    active.length > 0 && `${active.length} ${plural(active.length, ["действующее", "действующих", "действующих"])}`,
    ended.length > 0 &&
      `${ended.length} ${plural(ended.length, ["оконченное", "оконченных", "оконченных"])}${paid > 0 ? `, оплачено ${paid}` : ""}`,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="fssp-det">
      <button type="button" className="btn-link fssp-det__toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? "Скрыть производства" : `Производства: ${summary}`}
      </button>
      {open && (
        <div className="fssp-det__list">
          {active.length > 0 && <ProductionGroup title="Действующие" items={active} />}
          {ended.length > 0 && <ProductionGroup title="Оконченные" items={ended} />}
        </div>
      )}
    </div>
  );
}

function ProductionGroup({ title, items }: { title: string; items: Production[] }) {
  return (
    <section className="fssp-det__group">
      <h4 className="fssp-det__group-title">{title}</h4>
      <ul className="fssp-det__items">
        {items.map((p, i) => (
          <ProductionRow key={`${p.number}-${i}`} production={p} />
        ))}
      </ul>
    </section>
  );
}

function ProductionRow({ production: p }: { production: Production }) {
  const end = p.status === "active" ? null : fsspEndReason(p.end_reason);
  // Оконченные — жёлтым, даже оплаченные (решение владельца 23.11.2026): долг
  // был, это стоит видеть, но он закрыт; красным — действующие и банкротство.
  const tone = !end ? "active" : end.tone === "bad" ? "bad" : "warn";
  return (
    <li className={`fssp-det__item fssp-det__item--${tone}`}>
      {p.items.map((item, i) => (
        <div key={i} className="fssp-det__debt">
          <span className="fssp-det__subject">{item.subject}</span>
          {item.amount != null && <span className="fssp-det__amount">{formatAmount(item.amount)} ₽</span>}
        </div>
      ))}
      {end && (
        <div className={`fssp-det__end fssp-det__end--${tone}`}>
          Окончено{p.ended_at ? ` ${p.ended_at}` : ""}: {end.label}
          {p.end_reason && <span className="fssp-det__article"> ({p.end_reason})</span>}
        </div>
      )}
      <div className="fssp-det__meta">
        ИП {p.number}
        {p.date ? ` от ${p.date}` : ""}
        {p.document && ` · ${p.document}`}
        {p.department && ` · ${p.department}`}
      </div>
    </li>
  );
}

type BureauItem = {
  ep_number?: string | null;
  subject?: string | null;
  debt_total?: string | null;
  started?: string | null;
  ended?: string | null;
  office?: string | null;
};

const ruDate = (iso: string | null | undefined) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? "");
  return m ? `${m[3]}.${m[2]}.${m[1]}` : (iso ?? "");
};

/**
 * ФССП из отчёта бюро — те же производства с разворотом: действующие
 * (красным) и оконченные (жёлтым). У отчётов до 23.11.2026 оконченные
 * сохранены только числом.
 */
export function BureauFsspDetails({ parsed }: { parsed: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  const openItems = (Array.isArray(parsed.open_items) ? parsed.open_items : []) as BureauItem[];
  const closedItems = (Array.isArray(parsed.closed_items) ? parsed.closed_items : []) as BureauItem[];
  const closedCount = typeof parsed.closed_count === "number" ? parsed.closed_count : closedItems.length;
  if (openItems.length === 0 && closedItems.length === 0) return null;

  const row = (item: BureauItem, ended: boolean, key: number) => (
    <li key={key} className={`fssp-det__item fssp-det__item--${ended ? "warn" : "active"}`}>
      <div className="fssp-det__debt">
        <span className="fssp-det__subject">{item.subject ?? "предмет не указан"}</span>
        {item.debt_total != null && Number(item.debt_total) > 0 && (
          <span className="fssp-det__amount">{formatAmount(item.debt_total)} ₽</span>
        )}
      </div>
      {ended && (
        <div className="fssp-det__end fssp-det__end--warn">Окончено{item.ended ? ` ${ruDate(item.ended)}` : ""}</div>
      )}
      <div className="fssp-det__meta">
        {item.ep_number ? `ИП ${item.ep_number}` : "ИП"}
        {item.started ? ` от ${ruDate(item.started)}` : ""}
        {item.office && ` · ${item.office}`}
      </div>
    </li>
  );

  return (
    <div className="fssp-det">
      <button type="button" className="btn-link fssp-det__toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? "Скрыть" : "Подробнее"}
      </button>
      {open && (
        <div className="fssp-det__list">
          {openItems.length > 0 && (
            <section className="fssp-det__group">
              <h4 className="fssp-det__group-title">Действующие</h4>
              <ul className="fssp-det__items">{openItems.map((i, k) => row(i, false, k))}</ul>
            </section>
          )}
          {closedItems.length > 0 ? (
            <section className="fssp-det__group">
              <h4 className="fssp-det__group-title">Оконченные</h4>
              <ul className="fssp-det__items">{closedItems.map((i, k) => row(i, true, k))}</ul>
            </section>
          ) : (
            closedCount > 0 && <p className="fssp-det__meta">Оконченных: {closedCount} (подробности — в новых отчётах)</p>
          )}
        </div>
      )}
    </div>
  );
}
