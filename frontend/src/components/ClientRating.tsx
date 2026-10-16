import type { components } from "../api/schema";

type Rating = components["schemas"]["ClientRatingOut"];

function signed(points: number): string {
  return points > 0 ? `+${points}` : points < 0 ? `−${Math.abs(points)}` : "0";
}

// T23: рейтинг клиента по скоркарте заказчика («Рейтинг клиента.docx») —
// баллы по критериям складываются в одну сумму. Пока информационно: пороги
// суммы заказчик не назвал, на решение не влияет (affects_decision=false).
export function ClientRatingPanel({ rating }: { rating: Rating }) {
  const missing = rating.items.filter((i) => i.points == null).length;
  const tone = rating.total > 0 ? "up" : rating.total < 0 ? "down" : "same";
  return (
    <section className="section rating" data-testid="client-rating">
      <div className="rating__head">
        <h2>Рейтинг клиента</h2>
        {!rating.affects_decision && (
          <span className="rating__badge">информационно — на решение пока не влияет</span>
        )}
      </div>

      <div className="rating__summary">
        <div className={`rating__total rating__total--${tone}`}>
          {signed(rating.total)}
          <span className="rating__unit">баллов</span>
        </div>
        <p className="rating__explain">
          Сумма баллов по скоркарте заказчика: возраст, контакты, работа, прописка.
          {missing > 0 && ` По ${missing} из ${rating.items.length} критериев данных нет — сумма неполная.`}
        </p>
      </div>

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
                  <strong className={item.points > 0 ? "points--up" : item.points < 0 ? "points--down" : undefined}>
                    {signed(item.points)}
                  </strong>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {rating.guarantor_reasons.length > 0 && (
        <div className={rating.guarantor_present ? "rating__guarantor rating__guarantor--ok" : "rating__guarantor"}>
          <strong>
            {rating.guarantor_present ? "Нужен поручитель — в анкете он есть" : "Нужен поручитель — в анкете его нет"}
          </strong>
          <ul>
            {rating.guarantor_reasons.map((r) => (
              <li key={r.reason}>{r.reason}</li>
            ))}
          </ul>
        </div>
      )}

      {rating.not_yet_counted.length > 0 && (
        <p className="hint">Пока не учитывается (ждём уточнений заказчика): {rating.not_yet_counted.join("; ")}.</p>
      )}
    </section>
  );
}
