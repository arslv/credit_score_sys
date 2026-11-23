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
        <RatingScale min={rating.min_total} max={rating.max_total} value={rating.total} />
      </div>

      <div className="cr__items">
        <ul className="cr__grid">
          {visible.map((item) => (
            <li key={item.criterion} className={`cr-item cr-item--${tone(item.points)}`} title={item.label}>
              <span className="cr-item__title">{item.title}</span>
              <span className="cr-item__points">
                {item.points == null ? (
                  "нет данных"
                ) : (
                  <>
                    {signed(item.points)} <span className="cr-item__max">из {item.max_points}</span>
                  </>
                )}
              </span>
              <span className="cr-item__value">{item.points != null && item.value != null ? item.value : "—"}</span>
              {/* Сколько набрано из возможного по критерию — полоской. */}
              <span className="cr-item__bar" aria-hidden="true">
                {item.points != null && item.max_points > item.min_points && (
                  <span
                    className="cr-item__bar-fill"
                    style={{
                      width: `${Math.max(4, ((item.points - item.min_points) / (item.max_points - item.min_points)) * 100)}%`,
                    }}
                  />
                )}
              </span>
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

// Где клиент между худшим и лучшим возможным (по правилам) — полосой, а не
// текстом «шкала от … до …» (по отзыву 23.11.2026: было непонятно).
// Заливка — от нуля к метке: вправо зелёная, влево красная.
function RatingScale({ min, max, value }: { min: number; max: number; value: number }) {
  if (max <= min) return null;
  const pos = (v: number) => Math.min(100, Math.max(0, ((v - min) / (max - min)) * 100));
  const zero = pos(0);
  const at = pos(value);
  return (
    <div className="cr-scale" aria-label={`Рейтинг ${signed(value)}: от ${signed(min)} (худший) до ${signed(max)} (лучший)`}>
      <div className="cr-scale__track">
        <span
          className={`cr-scale__fill cr-scale__fill--${value >= 0 ? "up" : "down"}`}
          style={{ left: `${Math.min(zero, at)}%`, width: `${Math.abs(at - zero)}%` }}
        />
        <span className="cr-scale__zero" style={{ left: `${zero}%` }} />
        <span className={`cr-scale__marker cr-scale__marker--${tone(value)}`} style={{ left: `${at}%` }} />
      </div>
      <div className="cr-scale__labels">
        <span title="худший возможный">{signed(min)}</span>
        <span className="cr-scale__zero-label" style={{ left: `${zero}%` }}>
          0
        </span>
        <span title="лучший возможный">{signed(max)}</span>
      </div>
      <div className="cr-scale__hint">
        <span>← хуже</span>
        <span>надёжнее →</span>
      </div>
    </div>
  );
}
