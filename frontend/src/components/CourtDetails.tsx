import { useState } from "react";

// Дело из блока «Суды общей юрисдикции» отчёта бюро (Спектрум, «Премиум»).
// Поля — по реальному отчёту (сервер, 26.11.2026): номер, суд, регион,
// судья, категория, стороны, последнее событие, ссылка на карточку дела.
type CourtSide = { name?: string; category?: string; code?: string; type?: string; inn?: string; ogrn?: string };
type CourtCase = {
  uid?: string;
  number?: string;
  court_name?: string;
  region?: string;
  judge?: string;
  registration_date?: string;
  case_status?: string;
  case_type?: { name?: string; description?: string };
  articles?: string[];
  sides?: CourtSide[];
  last_event?: { name?: string; date?: string };
  events_count?: number;
  documents_count?: number;
  source_url?: string;
};

const ruDate = (value: string | null | undefined) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value ?? "");
  return m ? `${m[3]}.${m[2]}.${m[1]}` : (value ?? "");
};

// Сторона — клиент, если в её имени есть фамилия и имя клиента (бюро ищет
// по ФИО, бывают однофамильцы: стороны помогают отличить).
function isClient(side: CourtSide, clientName: string | null): boolean {
  if (!clientName || !side.name) return false;
  const name = side.name.toLowerCase();
  const [last, first] = clientName.toLowerCase().split(/\s+/);
  // Фамилия и имя целиком: «Иванов Тимур» ≠ «Иванов Тест». Сокращённое имя
  // («Иванов Т. Т.») — по первой букве с точкой.
  if (!last || !name.includes(last)) return false;
  if (!first) return true;
  return name.includes(first) || name.includes(`${first[0]}.`);
}

/**
 * «Суды общей юрисдикции» из отчёта бюро — дела с разворотом: номер и дата,
 * суд и регион, категория и статьи, стороны (клиент выделен), последнее
 * событие и ссылка на карточку дела на сайте суда. Поиск у бюро — по ФИО,
 * поэтому среди дел бывают однофамильцы.
 */
export function CourtDetails({ parsed, clientName }: { parsed: Record<string, unknown>; clientName: string | null }) {
  const [open, setOpen] = useState(false);
  const items = (Array.isArray(parsed.items) ? parsed.items : []) as CourtCase[];
  if (items.length === 0) return null;
  const total = typeof parsed.count === "number" ? parsed.count : items.length;

  return (
    <div className="fssp-det">
      <button type="button" className="btn-link fssp-det__toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? "Скрыть дела" : `Показать дела (${items.length})`}
      </button>
      {open && (
        <div className="fssp-det__list">
          <p className="court-det__note">
            Бюро ищет по ФИО — среди дел бывают однофамильцы: сверяйте регион и стороны.
            {total > items.length && ` Показаны ${items.length} из ${total}.`}
          </p>
          <ul className="fssp-det__items">
            {items.map((c, i) => (
              <li key={c.uid ?? `${c.number}-${i}`} className="fssp-det__item court-det__item">
                <div className="court-det__title">
                  {c.case_type?.name ?? "Дело"}
                  {c.case_type?.description && <span className="court-det__muted"> · {c.case_type.description}</span>}
                </div>
                {c.case_status && <div className="court-det__status">{c.case_status}</div>}
                {c.articles && c.articles.length > 0 && (
                  <div className="court-det__line">Статьи: {c.articles.join(", ")}</div>
                )}
                {c.sides && c.sides.length > 0 && (
                  <ul className="court-det__sides">
                    {c.sides.map((s, k) => (
                      <li key={k} className={isClient(s, clientName) ? "court-det__side court-det__side--client" : "court-det__side"}>
                        <span className="court-det__muted">{s.category ?? "сторона"}:</span> {s.name ?? "—"}
                      </li>
                    ))}
                  </ul>
                )}
                {c.last_event?.name && (
                  <div className="court-det__line">
                    Последнее: {c.last_event.name}
                    {c.last_event.date && ` · ${ruDate(c.last_event.date)}`}
                    {typeof c.events_count === "number" && c.events_count > 1 && (
                      <span className="court-det__muted"> (событий: {c.events_count})</span>
                    )}
                  </div>
                )}
                <div className="fssp-det__meta">
                  {c.number ? `№ ${c.number}` : "Дело"}
                  {c.registration_date && ` от ${ruDate(c.registration_date)}`}
                  {c.court_name && ` · ${c.court_name}`}
                  {c.region && ` · ${c.region}`}
                  {c.judge && ` · судья ${c.judge}`}
                </div>
                {c.source_url && /^https?:\/\//.test(c.source_url) && (
                  <a className="court-det__link" href={c.source_url} target="_blank" rel="noreferrer noopener">
                    Карточка дела на сайте суда ↗
                  </a>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
