// Заготовка под анализ заявки моделью ИИ — только интерфейс, данных нет.
// Модель обучим на истории заявок и платежей после закрытия первого этапа;
// до тех пор блок честно говорит, что анализ не проводится и на решение не влияет.

const PLANNED = [
  { label: "Вероятность невозврата", hint: "оценка риска по похожим клиентам" },
  { label: "Что повлияло на оценку", hint: "главные причины — понятным языком" },
  { label: "Похожие заявки", hint: "как платят клиенты с похожим профилем" },
];

export function AiAnalysisPanel() {
  return (
    <div className="ai-panel" aria-describedby="ai-panel-note">
      <p id="ai-panel-note" className="ai-panel__note">
        Анализ пока не проводится и на решение не влияет. Модель обучим на истории заявок и платежей после
        завершения первого этапа.
      </p>
      <ul className="ai-panel__list">
        {PLANNED.map((item) => (
          <li key={item.label} className="ai-panel__item">
            <span className="ai-panel__label">{item.label}</span>
            <span className="ai-panel__placeholder" aria-hidden="true" />
            <span className="ai-panel__hint">{item.hint}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
