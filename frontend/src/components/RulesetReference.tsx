import { useState } from "react";
import { RULESET_DOCS } from "../lib/rulesetDocs";
import { matches } from "./ui";

// Справочник полей YAML рядом с редактором: поиск по пути и описанию,
// разделы — свёрнутыми группами (при поиске раскрываются сами). Клик по полю —
// переход к нему в тексте YAML (onLocate возвращает false, если места нет).
export function RulesetReference({ onLocate }: { onLocate: (path: string) => boolean }) {
  const [search, setSearch] = useState("");
  const [missing, setMissing] = useState<string | null>(null);
  const query = search.trim();
  const groups = RULESET_DOCS.map((group) => ({
    ...group,
    entries: query ? group.entries.filter((e) => matches(query, e.path, e.text, e.note ?? "")) : group.entries,
  })).filter((group) => group.entries.length > 0);

  return (
    <aside className="yaml-ref" aria-label="Справочник полей">
      <div className="yaml-ref__head">
        <div className="yaml-ref__title">Справочник полей</div>
        <input
          type="search"
          className="yaml-ref__search"
          placeholder="Поле или слово: pti, просрочка, матрица…"
          aria-label="Поиск по справочнику полей"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      <div className="yaml-ref__body">
        {groups.length === 0 && <p className="muted">Ничего не нашлось.</p>}
        {groups.map((group) => (
          <details key={group.title} className="yaml-ref__group" open={Boolean(query) || undefined}>
            <summary>
              {group.title} <span className="muted">· {group.entries.length}</span>
            </summary>
            {group.intro && <p className="yaml-ref__intro">{group.intro}</p>}
            <dl className="yaml-ref__list">
              {group.entries.map((entry) => (
                <div key={entry.path} className="yaml-ref__entry">
                  <dt>
                    <button
                      type="button"
                      className="yaml-ref__jump"
                      title="Показать в YAML"
                      onClick={() => setMissing(onLocate(entry.path) ? null : entry.path)}
                    >
                      <code>{entry.path}</code>
                      <span className="yaml-ref__jump-icon" aria-hidden="true">↵</span>
                    </button>
                  </dt>
                  <dd>
                    {missing === entry.path && (
                      <span className="yaml-ref__missing" role="status">
                        В текущем YAML этого поля нет — его можно добавить.
                      </span>
                    )}
                    {entry.text}
                    {entry.note && <span className="yaml-ref__note">{entry.note}</span>}
                    {entry.example && <pre className="yaml-ref__example">{entry.example}</pre>}
                  </dd>
                </div>
              ))}
            </dl>
          </details>
        ))}
      </div>
    </aside>
  );
}
