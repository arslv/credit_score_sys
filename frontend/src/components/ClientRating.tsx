import { useState } from "react";
import type { components } from "../api/schema";

type Rating = components["schemas"]["ClientRatingOut"];

function signed(points: number): string {
  return points > 0 ? `+${points}` : points < 0 ? `−${Math.abs(points)}` : "0";
}

function tone(points: number | null | undefined): "up" | "down" | "zero" | "none" {
  if (points == null) return "none";
  return points > 0 ? "up" : points < 0 ? "down" : "zero";
}

// Сколько плиток видно сразу; остальные — по кнопке. Критериев станет
// больше, когда CRM пришлёт полную скоркарту.
const VISIBLE_ITEMS = 8;

// T23: рейтинг клиента по скоркарте («Рейтинг клиента.docx») — баллы по
// критериям складываются. Сумма и плитка на критерий: название, баллы и что
// учтено. Плитка одна на любой критерий — без шкал и порогов, они у
// критериев разные; правило начисления — в подсказке при наведении.
// Плитки — по силе влияния, без данных — в конце.
export function ClientRatingPanel({ rating }: { rating: Rating }) {
  const [showAll, setShowAll] = useState(false);
  const ordered = [
    ...rating.items.filter((i) => i.points != null).sort((a, b) => Math.abs(b.points!) - Math.abs(a.points!)),
    ...rating.items.filter((i) => i.points == null),
  ];
  const visible = showAll ? ordered : ordered.slice(0, VISIBLE_ITEMS);

  return (
    <section className="cr" id="client-rating" data-testid="client-rating" aria-labelledby="client-rating-title">
      <div className="cr__summary">
        <h2 id="client-rating-title" className="cr__title">
          Рейтинг клиента
        </h2>
        <div className="cr__total">
          <span className={`cr__number cr__number--${tone(rating.total)}`}>{signed(rating.total)}</span>
          <span className="cr__unit">баллов</span>
        </div>
      </div>

      <div className="cr__items">
        <ul className="cr__grid">
          {visible.map((item) => (
            <li key={item.criterion} className={`cr-item cr-item--${tone(item.points)}`} title={item.label}>
              <span className="cr-item__title">{item.title}</span>
              <span className="cr-item__points">{item.points == null ? "нет данных" : signed(item.points)}</span>
              {item.points != null && item.value != null && <span className="cr-item__value">{item.value}</span>}
            </li>
          ))}
        </ul>
        {ordered.length > VISIBLE_ITEMS && (
          <button type="button" className="link-button" aria-expanded={showAll} onClick={() => setShowAll((v) => !v)}>
            {showAll ? "Свернуть" : `Ещё ${ordered.length - VISIBLE_ITEMS}`}
          </button>
        )}
      </div>
    </section>
  );
}
