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

// T23: рейтинг клиента по скоркарте заказчика («Рейтинг клиента.docx») —
// баллы по критериям складываются. Сверху карточки заявки: сумма крупно и
// цветные плашки по критериям; таблица с правилами — по кнопке. Пока
// информационно: пороги суммы заказчик не назвал (affects_decision=false).
export function ClientRatingPanel({ rating }: { rating: Rating }) {
  const [open, setOpen] = useState(false);
  const missing = rating.items.filter((i) => i.points == null).length;
  const totalTone = tone(rating.total);
  const needGuarantor = rating.guarantor_reasons.length > 0;

  return (
    <section className={`rating-card rating-card--${totalTone}`} data-testid="client-rating">
      <div className="rating-card__head">
        <h2>Рейтинг клиента</h2>
        {!rating.affects_decision && <span className="rating__badge">информационно — на решение пока не влияет</span>}
      </div>

      <div className="rating-card__body">
        <div className="rating-card__total">
          <span className={`rating-card__number rating-card__number--${totalTone}`}>{signed(rating.total)}</span>
          <span className="rating__unit">баллов</span>
          {missing > 0 && (
            <span className="rating-card__partial">
              сумма неполная: нет данных по {missing} из {rating.items.length}
            </span>
          )}
        </div>
        <ul className="rating-chips">
          {rating.items.map((item) => (
            <li key={item.criterion} className={`rating-chip rating-chip--${tone(item.points)}`} title={item.label}>
              <span className="rating-chip__title">{item.title}</span>
              <span className="rating-chip__points">{item.points == null ? "нет данных" : signed(item.points)}</span>
            </li>
          ))}
        </ul>
      </div>

      {needGuarantor && (
        <p className={rating.guarantor_present ? "rating-card__guarantor rating-card__guarantor--ok" : "rating-card__guarantor"}>
          <strong>{rating.guarantor_present ? "Нужен поручитель — есть" : "Нужен поручитель — в анкете его нет"}:</strong>{" "}
          {rating.guarantor_reasons.map((r) => r.reason).join("; ")}
        </p>
      )}

      <button type="button" className="link-button" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? "Скрыть разбивку" : "Разбивка по критериям"}
      </button>
      {open && (
        <>
          <table className="table table--compact rating__table">
            <thead>
              <tr>
                <th>Критерий</th>
                <th>Учтено</th>
                <th>Правило</th>
                <th className="num">Баллы</th>
              </tr>
            </thead>
            <tbody>
              {rating.items.map((item) => (
                <tr key={item.criterion}>
                  <td>{item.title}</td>
                  <td className="cell-ellipsis">{item.value ?? <span className="muted">—</span>}</td>
                  <td className="muted">{item.label}</td>
                  <td className="num">
                    {item.points == null ? (
                      <span className="muted">нет данных</span>
                    ) : (
                      <strong className={`points--${tone(item.points)}`}>{signed(item.points)}</strong>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rating.not_yet_counted.length > 0 && (
            <p className="hint">Пока не учитывается (ждём уточнений заказчика): {rating.not_yet_counted.join("; ")}.</p>
          )}
        </>
      )}
    </section>
  );
}
