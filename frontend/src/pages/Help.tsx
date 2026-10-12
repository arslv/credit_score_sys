import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { EmptyState, PageHeader, SearchInput, Toolbar, matches } from "../components/ui";

// T17, часть 6: «Как работает система» — одна страница, на которую можно
// сослаться вместо того, чтобы пересказывать путь заявки в разговоре.
// T20: определения терминов живут только здесь — подсказок «?» у терминов
// в интерфейсе больше нет (одна подсказка на экран — у вердикта карточки).

const ROLE_ROWS: { role: string; label: string; sees: string }[] = [
  {
    role: "risk_manager",
    label: "Риск-менеджер",
    sees: "Видит все заявки и клиентов, меняет пороги и лимиты (раздел «Скоринг»), повторяет упавшие проверки, смотрит аналитику. Своя очередь «Требуют действия» — серая зона и случаи, когда оператор изменил статус.",
  },
  {
    role: "operator",
    label: "Оператор",
    sees: "Звонит клиенту в системе заказчика и нажимает «Рассмотреть», а в нашей админке проверяет ФССП по заявке — капчу решает человек. В карточке заявки видит ФИО и адрес заявителя и поручителя — без этого нельзя выбрать регион ФССП или отличить должника от однофамильца. В списке заявок тоже видит ФИО. Очередь — «позвонить» и «проверить ФССП».",
  },
  {
    role: "compliance",
    label: "Комплаенс",
    sees: "Снимает заморозку по совпадению с перечнем Росфинмониторинга, указывая причину. Видит фото и кредитный отчёт БКИ. Очередь — заявки, где заморозка ещё не снята.",
  },
  {
    role: "admin",
    label: "Администратор",
    sees: "Управляет сотрудниками, справочниками, импортом, смотрит аудит и состояние системы. Персданные заявителей недоступны нигде, включая карточку заявки.",
  },
  {
    role: "viewer",
    label: "Наблюдатель",
    sees: "Видит заявки, договоры и аналитику обезличенно — так же, как администратор. Ничего не меняет.",
  },
];

const GLOSSARY: { term: string; text: string }[] = [
  {
    term: "Балл БКИ (скор)",
    text: "Скоринговый балл кредитного бюро — не наш собственный расчёт. Шкала бюро: чем выше балл, тем надёжнее заёмщик по данным о прошлых кредитах. Появляется только на стадии Б, после запроса в БКИ.",
  },
  {
    term: "ПДН",
    text: "Показатель долговой нагрузки — доля дохода, которая уходит на платежи по долгам. До БКИ считается по заявленным клиентом данным, после БКИ — по факту. Выше порога — отказ или ручная проверка.",
  },
  {
    term: "Серая зона",
    text: "Решение, которое система не может принять сама: ни одобрить, ни отказать автоматически нельзя, нужно решение риск-менеджера или оператора. Бывает только на стадии Б — на стадии А исход «передано оператору», а не серая зона.",
  },
  {
    term: "Стоп-фактор",
    text: "Правило, которое само по себе ведёт к отказу или заморозке (например, совпадение по перечню, просроченный паспорт) — не балл и не сравнение порогов. Сработавший стоп-фактор виден в карточке заявки, в блоке «Почему такое решение».",
  },
  {
    term: "Стадия А / стадия Б",
    text: "Стадия А — сразу после подачи, без кредитной истории: только бесплатные локальные проверки (формат и срок паспорта, Росфинмониторинг, чёрный список). Одобрения на стадии А не бывает — только отказ или передача оператору. Стадия Б — после звонка оператора: полнота анкеты, шариат, лимиты, затем запрос в БКИ и финансовые правила.",
  },
  {
    term: "«Не вычислимо»",
    text: "Встречается при проверке новых правил на истории заявок (Скоринг → «Проверить на истории»): для старых снимков признаков нет нужного поля (правило появилось позже) — посчитать правило по ним нельзя. Это не значит, что оно бы не сработало.",
  },
  {
    term: "БКИ",
    text: "Бюро кредитных историй — источник кредитного отчёта и скорингового балла. Без согласия клиента или при недоступности БКИ одобрение невозможно — заявка уходит в серую зону.",
  },
  {
    term: "ФССП",
    text: "Федеральная служба судебных приставов — проверка на исполнительные производства (долги). Автоматически не вызывается ни на одной стадии: только по ручному действию, капча решается человеком. Без пройденной проверки ФССП (заявитель и поручитель, если есть) одобрение недоступно.",
  },
];

// T23: схема пути заявки — блоки со стрелками и исходами под шагом вместо
// нумерованного текста. Пунктир — шаг человека, а не системы. Исходы —
// строчными: заглавное «Серая зона» — термин глоссария ниже.
const FLOW: { name: string; what: string; actor?: boolean; outcomes: { label: string; tone: string }[] }[] = [
  { name: "Подача", what: "Заказчик присылает заявку и фото паспорта; паспорт распознаётся.", outcomes: [] },
  {
    name: "Стадия А",
    what: "Бесплатные локальные проверки: паспорт, Росфинмониторинг, чёрный список. ФНС — в фоне.",
    outcomes: [
      { label: "отказ", tone: "reject" },
      { label: "передано оператору", tone: "progress" },
    ],
  },
  {
    name: "Звонок",
    what: "Оператор заказчика звонит клиенту и жмёт «Рассмотреть»; ФССП проверяет наш оператор.",
    actor: true,
    outcomes: [],
  },
  {
    name: "Стадия Б",
    what: "Полнота анкеты, шариат, лимиты, запрос в БКИ, долговая нагрузка и балл.",
    outcomes: [
      { label: "одобрено", tone: "approve" },
      { label: "серая зона", tone: "gray" },
      { label: "отказ", tone: "reject" },
    ],
  },
  {
    name: "Ответ заказчику",
    what: "Решение уходит в систему заказчика с причиной человеческим языком.",
    outcomes: [],
  },
];

const SECTIONS = [
  { id: "path", title: "Путь заявки" },
  { id: "levers", title: "Три рычага, которые влияют на решение" },
  { id: "roles", title: "Роли" },
  { id: "terms", title: "Термины" },
];

function Highlight({ text, query }: { text: string; query: string }) {
  const q = query.trim();
  if (!q) return <>{text}</>;
  const index = text.toLowerCase().indexOf(q.toLowerCase());
  if (index < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, index)}
      <mark>{text.slice(index, index + q.length)}</mark>
      {text.slice(index + q.length)}
    </>
  );
}

export function HelpPage() {
  const { employee } = useAuth();
  const [termQuery, setTermQuery] = useState("");
  const [activeSection, setActiveSection] = useState(SECTIONS[0].id);

  // Подсветка текущего раздела в оглавлении при прокрутке.
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActiveSection(visible[0].target.id);
      },
      { rootMargin: "0px 0px -60% 0px" },
    );
    SECTIONS.forEach(({ id }) => {
      const element = document.getElementById(id);
      if (element) observer.observe(element);
    });
    return () => observer.disconnect();
  }, []);

  const terms = GLOSSARY.filter((g) => matches(termQuery, g.term, g.text));

  return (
    <section>
      <PageHeader
        title="Как работает система"
        description="Сервис автоматических решений по заявкам на рассрочку. Работает рядом с системой заказчика: она сама присылает нам заявки, мы проверяем, принимаем решение и отправляем ответ обратно в её систему."
      />
      <div className="help-layout">
        <nav className="help-toc" aria-label="Оглавление справки">
          {SECTIONS.map((section) => (
            <a
              key={section.id}
              href={"#" + section.id}
              className={activeSection === section.id ? "help-toc__active" : undefined}
              onClick={(event) => {
                event.preventDefault();
                document.getElementById(section.id)?.scrollIntoView({ behavior: "smooth", block: "start" });
                setActiveSection(section.id);
              }}
            >
              {section.title}
            </a>
          ))}
        </nav>

        <div>
          <section id="path" className="help-section">
            <h2>Путь заявки</h2>
            <div className="flow" role="list">
              {FLOW.map((step) => (
                <div key={step.name} className="flow__step" role="listitem">
                  <div className={step.actor ? "flow__box flow__box--actor" : "flow__box"}>
                    <div className="flow__name">{step.name}</div>
                    <div className="flow__what">{step.what}</div>
                  </div>
                  {step.outcomes.length > 0 && (
                    <div className="flow__outcomes">
                      {step.outcomes.map((o) => (
                        <span key={o.label} className={"status-pill status-pill--" + o.tone}>
                          {o.label}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
            <p className="hint">
              Одобрения на стадии А не бывает. Без кредитной истории или без пройденной проверки ФССП (заявитель и
              поручитель) одобрить нельзя — только серая зона.
            </p>
          </section>

          <section id="levers" className="help-section">
            <h2>Три рычага, которые влияют на решение</h2>
            <div className="help-levers">
              <div className="card">
                <h3>Правила и пороги</h3>
                <p className="muted">
                  Пороги балла БКИ, лимиты сделки, долговая нагрузка — в версии правил, не в коде. Каждое решение
                  хранит версию, по которой принято.
                </p>
                {employee?.role === "risk_manager" ? (
                  <Link to="/rulesets">Изменить пороги →</Link>
                ) : (
                  <span className="muted">Меняет риск-менеджер.</span>
                )}
              </div>
              <div className="card">
                <h3>Ручное решение оператора</h3>
                <p className="muted">
                  Оператор заказчика может сменить статус сам после звонка. Расхождение с системой видно в карточке и
                  в списке («Оператор изменил статус»).
                </p>
                <span className="muted">Меняется в системе заказчика, не у нас.</span>
              </div>
              <div className="card">
                <h3>Комплаенс</h3>
                <p className="muted">
                  Совпадение с перечнем Росфинмониторинга замораживает клиента, пока комплаенс не снимет заморозку с
                  причиной.
                </p>
                {employee?.role === "compliance" ? (
                  <Link to="/compliance">Открыть очередь →</Link>
                ) : (
                  <span className="muted">Снимает комплаенс.</span>
                )}
              </div>
            </div>
          </section>

          <section id="roles" className="help-section">
            <h2>Роли</h2>
            <table className="table help-roles">
              <thead>
                <tr>
                  <th>Роль</th>
                  <th>Что видит и делает</th>
                </tr>
              </thead>
              <tbody>
                {ROLE_ROWS.map((row) => (
                  <tr key={row.role} className={employee?.role === row.role ? "help-role--active" : undefined}>
                    <td className="cell-top">
                      {row.label}
                      {employee?.role === row.role && <span className="muted"> (вы)</span>}
                    </td>
                    <td className="muted">{row.sees}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section id="terms" className="help-section">
            <h2>Термины</h2>
            <Toolbar>
              <SearchInput value={termQuery} onChange={setTermQuery} placeholder="Найти термин" />
              {termQuery && (
                <span className="toolbar__count">
                  Найдено: {terms.length} из {GLOSSARY.length}
                </span>
              )}
            </Toolbar>
            {terms.length === 0 ? (
              <EmptyState title="Такого термина нет" hint="Попробуйте другое слово — например, «ПДН» или «серая зона»." />
            ) : (
              <dl className="help-glossary-grid">
                {terms.map((g) => (
                  <div key={g.term}>
                    <dt>{termQuery ? <Highlight text={g.term} query={termQuery} /> : g.term}</dt>
                    <dd>
                      <Highlight text={g.text} query={termQuery} />
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </section>
        </div>
      </div>
    </section>
  );
}
