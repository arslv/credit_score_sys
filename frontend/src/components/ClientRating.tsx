import { Fragment, useState } from "react";
import type { components } from "../api/schema";

type Rating = components["schemas"]["ClientRatingOut"];

function signed(points: number): string {
  return points > 0 ? `+${points}` : points < 0 ? `−${Math.abs(points)}` : "0";
}

function tone(points: number | null | undefined): "up" | "down" | "zero" | "none" {
  if (points == null) return "none";
  return points > 0 ? "up" : points < 0 ? "down" : "zero";
}

// T23: рейтинг клиента по скоркарте («Рейтинг клиента.docx») — баллы по
// критериям складываются. Панель в правой колонке карточки: сумма крупно и
// строка на каждый критерий; правила начисления — по кнопке. Пока
// информационно: пороги суммы не названы (affects_decision=false).
export function ClientRatingPanel({ rating }: { rating: Rating }) {
  const [open, setOpen] = useState(false);
  const missing = rating.items.filter((i) => i.points == null).length;
  const totalTone = tone(rating.total);
  const needGuarantor = rating.guarantor_reasons.length > 0;

  return (
    <section className="ac-panel rating" data-testid="client-rating">
      <h2 className="ac-panel__title">Рейтинг клиента</h2>
      {!rating.affects_decision && <p className="rating__badge">информационно — на решение пока не влияет</p>}

      <div className="rating__total">
        <span className={`rating__number rating__number--${totalTone}`}>{signed(rating.total)}</span>
        <span className="rating__unit">баллов</span>
      </div>
      {missing > 0 && (
        <p className="rating__partial">
          сумма неполная: нет данных по {missing} из {rating.items.length}
        </p>
      )}

      <ul className="rating-list">
        {rating.items.map((item) => (
          <li key={item.criterion} className={`rating-chip rating-chip--${tone(item.points)}`} title={item.label}>
            <span className="rating-chip__title">{item.title}</span>
            <span className="rating-chip__points">{item.points == null ? "нет данных" : signed(item.points)}</span>
          </li>
        ))}
      </ul>

      {needGuarantor && (
        <p className={rating.guarantor_present ? "rating__guarantor rating__guarantor--ok" : "rating__guarantor"}>
          <strong>{rating.guarantor_present ? "Нужен поручитель — есть" : "Нужен поручитель — в анкете его нет"}:</strong>{" "}
          {rating.guarantor_reasons.map((r) => r.reason).join("; ")}
        </p>
      )}

      <button type="button" className="link-button" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? "Скрыть разбивку" : "Разбивка по критериям"}
      </button>
      {open && (
        <>
          {/* Узкая колонка: критерий и баллы — строкой, что учтено и правило — под ней. */}
          <table className="table table--compact rating__table">
            <thead>
              <tr>
                <th>Критерий</th>
                <th className="num">Баллы</th>
              </tr>
            </thead>
            <tbody>
              {rating.items.map((item) => (
                <Fragment key={item.criterion}>
                  <tr className="rating__table-main">
                    <td>{item.title}</td>
                    <td className="num">
                      {item.points == null ? (
                        <span className="muted">нет данных</span>
                      ) : (
                        <strong className={`points--${tone(item.points)}`}>{signed(item.points)}</strong>
                      )}
                    </td>
                  </tr>
                  <tr className="rating__table-rule">
                    <td colSpan={2}>
                      {item.value != null && <>{item.value} · </>}
                      {item.label}
                    </td>
                  </tr>
                </Fragment>
              ))}
            </tbody>
          </table>
          {rating.not_yet_counted.length > 0 && (
            <p className="hint">Пока не учитывается (правила ещё уточняются): {rating.not_yet_counted.join("; ")}.</p>
          )}
        </>
      )}
    </section>
  );
}
