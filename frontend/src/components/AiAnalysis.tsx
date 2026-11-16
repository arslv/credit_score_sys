// «Анализ ИИ» — место под ответ модели по заявке (решение владельца
// 16.11.2026: показать, что модуль будет). Модели и бэкенда пока нет — её
// строят после закрытия первой фазы; здесь только заглушка «в разработке».
const PLANNED = [
  { title: "Вероятность дефолта", hint: "оценка риска по заявке и истории платежей" },
  { title: "Что повлияло", hint: "главные факторы в пользу и против" },
  { title: "Похожие заявки", hint: "как закончились заявки с таким профилем" },
];

export function AiAnalysis() {
  return (
    <section className="pf-card ai" aria-labelledby="ai-title">
      <header className="ai__head">
        <span className="ai__mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z" />
            <path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z" />
          </svg>
        </span>
        <h2 id="ai-title" className="pf-section-title ai__title">
          Анализ ИИ
        </h2>
        <span className="ai__badge">в разработке</span>
      </header>
      <ul className="ai__grid">
        {PLANNED.map((item) => (
          <li key={item.title} className="ai__tile">
            <span className="ai__tile-title">{item.title}</span>
            <span className="ai__tile-hint">{item.hint}</span>
            <span className="ai__shimmer" aria-hidden="true" />
          </li>
        ))}
      </ul>
    </section>
  );
}
