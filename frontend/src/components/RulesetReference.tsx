import { useState } from "react";
import { RULESET_DOCS } from "../lib/rulesetDocs";
import { matches } from "./ui";

// Справочник полей YAML рядом с редактором: поиск по пути и описанию,
// разделы — свёрнутыми группами (при поиске раскрываются сами).
export function RulesetReference() {
  const [search, setSearch] = useState("");
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
                    <code>{entry.path}</code>
                  </dt>
                  <dd>
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
