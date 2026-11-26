import { useState } from "react";

// Дело из блока «Суды общей юрисдикции» отчёта бюро (Спектрум, «Премиум»).
// Поля — по реальному отчёту (сервер, 26.11.2026): номер, суд, регион,
// судья, вид дела, стороны, последнее событие, ссылка на карточку дела.
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

// Сторона — клиент, если в её имени фамилия и имя клиента целиком
// («Иванов Тимур» ≠ «Иванов Тест»); сокращённое имя — по первой букве с точкой.
function isClient(side: CourtSide, clientName: string | null): boolean {
  if (!clientName || !side.name) return false;
  const name = side.name.toLowerCase();
  const [last, first] = clientName.toLowerCase().split(/\s+/);
  if (!last || !name.includes(last)) return false;
  if (!first) return true;
  return name.includes(first) || name.includes(`${first[0]}.`);
}

// Вид дела: бюро присылает код (CIVIL) и описание («Гражданские дела») —
// показываем описание.
const caseKind = (c: CourtCase) => c.case_type?.description || c.case_type?.name || "Дело";

const clientRole = (c: CourtCase, clientName: string | null) =>
  c.sides?.find((s) => isClient(s, clientName))?.category ?? null;

// Бюро присылает одно и то же дело дважды (разные записи по одному номеру).
function dedupe(items: CourtCase[]): CourtCase[] {
  const seen = new Set<string>();
  return items.filter((c) => {
    const key = `${c.number ?? c.uid}|${c.court_name ?? ""}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function countBy(values: string[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const v of values) counts.set(v, (counts.get(v) ?? 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

/**
 * «Суды общей юрисдикции» из отчёта бюро: сводка (виды дел, роль клиента) и
 * компактный список — по строке на дело, подробности по клику; длинный
 * список прокручивается внутри. Поиск у бюро — по ФИО, бывают однофамильцы.
 */
export function CourtDetails({ parsed, clientName }: { parsed: Record<string, unknown>; clientName: string | null }) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const raw = (Array.isArray(parsed.items) ? parsed.items : []) as CourtCase[];
  if (raw.length === 0) return null;
  const items = dedupe(raw);
  const total = typeof parsed.count === "number" ? parsed.count : raw.length;
  const kinds = countBy(items.map(caseKind));
  const roles = countBy(items.map((c) => clientRole(c, clientName)).filter((r): r is string => Boolean(r)));

  return (
    <div className="fssp-det court">
      <button type="button" className="btn-link fssp-det__toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? "Скрыть дела" : `Показать дела (${items.length})`}
      </button>
      {open && (
        <div className="court__body">
          <div className="court__summary">
            {kinds.map(([kind, n]) => (
              <span key={kind} className="court__chip">
                {kind} <b>{n}</b>
              </span>
            ))}
            {roles.map(([role, n]) => (
              <span key={role} className="court__chip court__chip--role">
                клиент — {role.toLowerCase()} <b>{n}</b>
              </span>
            ))}
          </div>
          <p className="court__note">
            Поиск по ФИО — бывают однофамильцы.
            {total > raw.length && ` Показаны ${items.length} из ${total}.`}
          </p>
          <ul className="court__list">
            {items.map((c, i) => {
              const id = c.uid ?? `${c.number}-${i}`;
              const isOpen = expanded === id;
              const role = clientRole(c, clientName);
              return (
                <li key={id} className={isOpen ? "court__case court__case--open" : "court__case"}>
                  <button
                    type="button"
                    className="court__head"
                    aria-expanded={isOpen}
                    onClick={() => setExpanded(isOpen ? null : id)}
                  >
                    <span className="court__kind">{caseKind(c)}</span>
                    {c.registration_date && <span className="court__date">{ruDate(c.registration_date)}</span>}
                    <span className="court__meta">
                      {c.number ? `№ ${c.number}` : "без номера"}
                      {role && <span className="court__role">{role}</span>}
                      {c.case_status && <span className="court__status">{c.case_status}</span>}
                    </span>
                    <span className="court__chevron" aria-hidden="true">
                      ›
                    </span>
                  </button>
                  {isOpen && (
                    <div className="court__more">
                      {c.articles && c.articles.length > 0 && <div>Статьи: {c.articles.join(", ")}</div>}
                      {c.sides && c.sides.length > 0 && (
                        <ul className="court__sides">
                          {c.sides.map((s, k) => (
                            <li key={k} className={isClient(s, clientName) ? "court__side court__side--client" : "court__side"}>
                              <span className="court__muted">{s.category ?? "сторона"}:</span> {s.name ?? "—"}
                            </li>
                          ))}
                        </ul>
                      )}
                      {c.last_event?.name && (
                        <div>
                          Последнее: {c.last_event.name}
                          {c.last_event.date && ` · ${ruDate(c.last_event.date)}`}
                          {typeof c.events_count === "number" && c.events_count > 1 && (
                            <span className="court__muted"> (событий: {c.events_count})</span>
                          )}
                        </div>
                      )}
                      <div className="court__muted">
                        {[c.court_name, c.region, c.judge && `судья ${c.judge}`].filter(Boolean).join(" · ")}
                      </div>
                      {c.source_url && /^https?:\/\//.test(c.source_url) && (
                        <a href={c.source_url} target="_blank" rel="noreferrer noopener">
                          Карточка дела на сайте суда ↗
                        </a>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
