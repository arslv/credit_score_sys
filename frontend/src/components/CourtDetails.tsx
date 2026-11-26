import { useState } from "react";
import { explainArticle } from "../lib/courtArticles";

// Дело из блока «Суды общей юрисдикции» отчёта бюро (Спектрум, «Премиум»).
// Поля — по реальному отчёту (сервер, 26.11.2026): номер, вид дела, статьи,
// стороны, статус, последнее событие, ссылка на карточку дела. Суммы иска
// бюро не присылает. Суд, регион и судью не показываем — для решения по
// рассрочке они ничего не дают (решение владельца 26.11.2026).
type CourtSide = { name?: string; category?: string; code?: string; type?: string; inn?: string; ogrn?: string };
type CourtCase = {
  uid?: string;
  number?: string;
  court_name?: string;
  registration_date?: string;
  case_status?: string;
  case_type?: { name?: string; description?: string };
  articles?: string[];
  sides?: CourtSide[];
  last_event?: { name?: string; date?: string };
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

// Вид дела: бюро присылает код (CIVIL) и описание («Гражданские дела»).
const KIND_SHORT: Record<string, string> = {
  "Гражданские дела": "Гражданское",
  "Административные дела": "Административное",
  "Уголовные дела": "Уголовное",
};
const caseKind = (c: CourtCase) => {
  const kind = c.case_type?.description || c.case_type?.name || "Дело";
  return KIND_SHORT[kind] ?? kind;
};

type Gist = {
  /** Суть дела одной фразой — заголовок строки. */
  title: string;
  /** Пояснение мелко: статья или вторая сторона. */
  note: string | null;
  /** Дело про деньги и долги — выделяем красным. */
  money: boolean;
};

/**
 * Суть дела вместо «Административное · Ответчик»: по статье КоАП/УК — что
 * за нарушение; по гражданскому — кто и к кому (судебный приказ — это
 * взыскание бесспорного долга).
 */
function gist(c: CourtCase, clientName: string | null): Gist {
  const kind = caseKind(c);
  const article = c.articles?.[0] ?? null;
  if (kind === "Административное" || kind === "Уголовное") {
    const code = kind === "Уголовное" ? "УК" : "КоАП";
    const ex = article ? explainArticle(article, code) : null;
    const ref = article ? `${article.replace(/^ст\.?\s*/i, "ст. ")} ${code}` : null;
    return { title: ex?.label ?? kind, note: ref, money: Boolean(ex?.money) };
  }
  const other = c.sides?.find((s) => !isClient(s, clientName) && s.name) ?? null;
  const who = other ? `${other.category ? `${other.category}: ` : ""}${other.name}` : null;
  const role = c.sides?.find((s) => isClient(s, clientName))?.category?.toLowerCase() ?? "";
  const status = `${c.case_status ?? ""} ${c.last_event?.name ?? ""}`.toLowerCase();
  if (status.includes("приказ")) {
    return { title: "Взыскание долга", note: who, money: true };
  }
  if (/истец|взыскатель|заявитель/.test(role)) {
    return { title: "Иск клиента", note: who, money: false };
  }
  if (/ответчик|должник/.test(role)) {
    return { title: "Иск к клиенту", note: who, money: true };
  }
  return { title: kind, note: who, money: false };
}

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
 * «Суды общей юрисдикции» из отчёта бюро: сводка и компактный список —
 * строка на дело (суть · дата), по клику итог, ход дела и ссылка на суд;
 * длинный список прокручивается внутри. Поиск у бюро — по ФИО, бывают
 * однофамильцы.
 */
export function CourtDetails({ parsed, clientName }: { parsed: Record<string, unknown>; clientName: string | null }) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const raw = (Array.isArray(parsed.items) ? parsed.items : []) as CourtCase[];
  if (raw.length === 0) return null;
  const items = dedupe(raw);
  const total = typeof parsed.count === "number" ? parsed.count : raw.length;
  const gists = items.map((c) => gist(c, clientName));
  const kinds = countBy(items.map(caseKind));
  const money = gists.filter((g) => g.money).length;

  return (
    <div className="fssp-det court">
      <button type="button" className="btn-link fssp-det__toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? "Скрыть дела" : `Показать дела (${items.length})`}
      </button>
      {open && (
        <div className="court__body">
          <p className="court__summary">
            {kinds.map(([kind, n]) => `${kind} ${n}`).join(" · ")}
            {money > 0 && <span className="court__money"> · про долги и деньги {money}</span>}
            {total > raw.length && <span> · показаны {items.length} из {total}</span>}
          </p>
          <ul className="court__list">
            {items.map((c, i) => {
              const id = c.uid ?? `${c.number}-${i}`;
              const isOpen = expanded === id;
              const g = gists[i];
              return (
                <li key={id} className={isOpen ? "court__case court__case--open" : "court__case"}>
                  <button
                    type="button"
                    className="court__head"
                    aria-expanded={isOpen}
                    onClick={() => setExpanded(isOpen ? null : id)}
                  >
                    <span className="court__kind">
                      <span className={g.money ? "court__money" : undefined}>{g.title}</span>
                      {g.note && <span className="court__note">{g.note}</span>}
                    </span>
                    {c.registration_date && <span className="court__date">{ruDate(c.registration_date)}</span>}
                    <span className="court__chevron" aria-hidden="true">
                      ›
                    </span>
                  </button>
                  {isOpen && (
                    <dl className="court__more">
                      {c.articles && c.articles.length > 1 && (
                        <>
                          <dt>Статьи</dt>
                          <dd>{c.articles.join(", ")}</dd>
                        </>
                      )}
                      {c.case_status && (
                        <>
                          <dt>Итог</dt>
                          <dd>{c.case_status}</dd>
                        </>
                      )}
                      {c.last_event?.name && (
                        <>
                          <dt>Последнее</dt>
                          <dd>
                            {c.last_event.name}
                            {c.last_event.date && <span className="court__muted"> · {ruDate(c.last_event.date)}</span>}
                          </dd>
                        </>
                      )}
                      {c.source_url && /^https?:\/\//.test(c.source_url) ? (
                        <a href={c.source_url} target="_blank" rel="noreferrer noopener">
                          Дело {c.number ? `№ ${c.number}` : ""} на сайте суда ↗
                        </a>
                      ) : (
                        c.number && <span className="court__muted">Дело № {c.number}</span>
                      )}
                    </dl>
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
