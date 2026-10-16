import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { EmptyState, Highlight, PageHeader, SearchInput, Toolbar, matches } from "../components/ui";

// T17, часть 6: «Как работает система» — одна страница, на которую можно
// сослаться вместо того, чтобы пересказывать путь заявки в разговоре.
// T20: определения терминов живут только здесь — подсказок «?» у терминов
// в интерфейсе больше нет (одна подсказка на экран — у вердикта карточки).

// T23 (решение владельца): ролей три. Обязанности бывших «риск-менеджера»
// и «комплаенса» — у администратора, проверку по перечням делает и оператор.
// Персданные (ФИО, фото паспорта, кредитный отчёт) видят все три роли.
const ROLE_ROWS: { role: string; label: string; sees: string }[] = [
  {
    role: "admin",
    label: "Администратор",
    sees: "Всё, что есть в системе: меняет пороги и лимиты (раздел «Скоринг»), повторяет упавшие проверки, смотрит аналитику, разбирает совпадения с перечнем Росфинмониторинга, проверяет ФССП. Управляет сотрудниками, справочниками, импортом, смотрит аудит и состояние системы. Очередь «Требуют действия» — серая зона, случаи, когда оператор изменил статус, и совпадения с перечнем.",
  },
  {
    role: "operator",
    label: "Оператор",
    sees: "Звонит клиенту в системе заказчика и нажимает «Рассмотреть», а в нашей админке проверяет ФССП по заявке (капчу решает человек) и разбирает совпадения с перечнем Росфинмониторинга. Очередь — «позвонить», «проверить ФССП» и «проверить совпадение с перечнем».",
  },
  {
    role: "viewer",
    label: "Наблюдатель",
    sees: "Видит заявки, клиентов, договоры и аналитику. Ничего не меняет.",
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
    text: "Решение, которое система не может принять сама: ни одобрить, ни отказать автоматически нельзя, нужно решение человека. Бывает только на стадии Б — на стадии А исход «передано оператору», а не серая зона.",
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

  // Совпадение в названии термина — выше совпадения только в описании.
  const terms = GLOSSARY.filter((g) => matches(termQuery, g.term, g.text)).sort(
    (a, b) => Number(matches(termQuery, b.term)) - Number(matches(termQuery, a.term)),
  );

  // Поиск стоит внизу страницы: при фильтрации список короче, страница
  // уменьшалась, браузер подтягивал прокрутку — экран «прыгал». Держим
  // высоту области терминов равной полному списку (меряем без фильтра).
  const termsRef = useRef<HTMLDivElement>(null);
  const [termsMinHeight, setTermsMinHeight] = useState<number | undefined>(undefined);
  useLayoutEffect(() => {
    if (termQuery || !termsRef.current) return;
    const measure = () => termsRef.current && setTermsMinHeight(termsRef.current.offsetHeight);
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [termQuery]);

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
                {employee?.role === "admin" ? (
                  <Link to="/rulesets">Изменить пороги →</Link>
                ) : (
                  <span className="muted">Меняет администратор.</span>
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
                <h3>Проверка по перечням</h3>
                <p className="muted">
                  Если ФИО клиента совпало с перечнем Росфинмониторинга (террористы и экстремисты), клиент
                  замораживается. Человек проверяет, он ли это или однофамилец, и снимает заморозку с причиной.
                </p>
                {employee?.role === "admin" || employee?.role === "operator" ? (
                  <Link to="/compliance">Открыть проверку →</Link>
                ) : (
                  <span className="muted">Разбирают администратор и оператор.</span>
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
            <div ref={termsRef} style={{ minHeight: termQuery ? termsMinHeight : undefined }}>
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
            </div>
          </section>
        </div>
      </div>
    </section>
  );
}
