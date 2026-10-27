import { useState } from "react";
import type { components } from "../api/schema";

type Rating = components["schemas"]["ClientRatingOut"];
type Item = Rating["items"][number];

function signed(points: number): string {
  return points > 0 ? `+${points}` : points < 0 ? `−${Math.abs(points)}` : "0";
}

function tone(points: number | null | undefined): "up" | "down" | "zero" | "none" {
  if (points == null) return "none";
  return points > 0 ? "up" : points < 0 ? "down" : "zero";
}

// Сколько плиток критериев видно сразу; остальные — по кнопке. Критериев
// станет больше, когда CRM пришлёт полную скоркарту, — раздел не должен
// превращаться в простыню.
const VISIBLE_ITEMS = 8;

// T23: рейтинг клиента по скоркарте («Рейтинг клиента.docx») — баллы по
// критериям складываются. Слева — сумма и что на неё повлияло сильнее всего,
// справа — плитка на каждый критерий: что учтено, по какому правилу, сколько
// баллов. Плитки — по силе влияния, без данных — в конце. Пока информационно:
// пороги суммы не названы (affects_decision=false).
export function ClientRatingPanel({ rating }: { rating: Rating }) {
  const [showAll, setShowAll] = useState(false);
  const counted = rating.items.filter((i) => i.points != null);
  const missing = rating.items.filter((i) => i.points == null);
  const ordered = [
    ...[...counted].sort((a, b) => Math.abs(b.points!) - Math.abs(a.points!)),
    ...missing,
  ];
  const visible = showAll ? ordered : ordered.slice(0, VISIBLE_ITEMS);
  const hidden = ordered.length - visible.length;
  // Длина полоски — доля от самого «весомого» критерия этой заявки.
  const maxAbs = Math.max(1, ...counted.map((i) => Math.abs(i.points!)));
  const plus = counted.filter((i) => i.points! > 0);
  const minus = counted.filter((i) => i.points! < 0);
  const sum = (items: Item[]) => items.reduce((acc, i) => acc + i.points!, 0);
  const needGuarantor = rating.guarantor_reasons.length > 0;

  return (
    <section className="cr" id="client-rating" data-testid="client-rating" aria-labelledby="client-rating-title">
      <div className="cr__summary">
        <h2 id="client-rating-title" className="cr__title">
          Рейтинг клиента
        </h2>
        {!rating.affects_decision && <p className="cr__badge">информационно — на решение пока не влияет</p>}

        <div className="cr__total">
          <span className={`cr__number cr__number--${tone(rating.total)}`}>{signed(rating.total)}</span>
          <span className="cr__unit">баллов</span>
        </div>
        <p className="cr__counted">
          учтено {counted.length} из {rating.items.length} критериев
        </p>

        <dl className="cr__split">
          {plus.length > 0 && (
            <div className="cr__split-row cr__split-row--up">
              <dt>Добавили</dt>
              <dd>
                {signed(sum(plus))} · {plus.map((i) => i.title).join(", ")}
              </dd>
            </div>
          )}
          {minus.length > 0 && (
            <div className="cr__split-row cr__split-row--down">
              <dt>Сняли</dt>
              <dd>
                {signed(sum(minus))} · {minus.map((i) => i.title).join(", ")}
              </dd>
            </div>
          )}
          {missing.length > 0 && (
            <div className="cr__split-row cr__split-row--none">
              <dt>Нет данных</dt>
              <dd>{missing.map((i) => i.title).join(", ")}</dd>
            </div>
          )}
        </dl>

        {needGuarantor && (
          <p className={rating.guarantor_present ? "cr__guarantor cr__guarantor--ok" : "cr__guarantor"}>
            <strong>{rating.guarantor_present ? "Нужен поручитель — есть" : "Нужен поручитель — в анкете его нет"}:</strong>{" "}
            {rating.guarantor_reasons.map((r) => r.reason).join("; ")}
          </p>
        )}
      </div>

      <div className="cr__items">
        <ul className="cr__grid">
          {visible.map((item) => (
            <li key={item.criterion} className={`cr-item cr-item--${tone(item.points)}`}>
              <div className="cr-item__head">
                <span className="cr-item__title">{item.title}</span>
                <span className="cr-item__points">{item.points == null ? "нет данных" : signed(item.points)}</span>
              </div>
              {/* Полоска от центра: вправо — плюс, влево — минус. */}
              <span className="cr-item__bar" aria-hidden="true">
                {item.points != null && item.points !== 0 && (
                  <i style={{ width: `${(Math.abs(item.points) / maxAbs) * 50}%` }} />
                )}
              </span>
              <p className="cr-item__rule">
                {item.value != null && <b>{item.value}</b>}
                {item.value != null && " · "}
                {item.label}
              </p>
            </li>
          ))}
        </ul>
        {(hidden > 0 || showAll) && ordered.length > VISIBLE_ITEMS && (
          <button type="button" className="link-button" aria-expanded={showAll} onClick={() => setShowAll((v) => !v)}>
            {showAll ? "Свернуть" : `Ещё ${hidden} критериев`}
          </button>
        )}
        {rating.not_yet_counted.length > 0 && (
          <p className="cr__later">Пока не учитывается (правила уточняются): {rating.not_yet_counted.join("; ")}.</p>
        )}
      </div>
    </section>
  );
}
