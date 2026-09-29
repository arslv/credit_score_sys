import { useState } from "react";
import { flatten } from "../lib/flatten";
import { humanizeFieldPath } from "../lib/labels";
import { formatDateTime } from "../lib/format";
import type { components } from "../api/schema";

type ExternalSnapshotOut = components["schemas"]["ExternalSnapshotOut"];

// Сырой снимок от заказчика — это весь ответ их API (id/status/photos/
// comments/status_history/technical и т.п.), а не только то, что вписали
// заявитель/оператор. Остальное уже показано в других блоках карточки
// (История, Документы) — здесь оставляем только форму и поля стадии Б,
// иначе список из полусотни технических строк топит те несколько, что
// реально менял оператор.
function anketaFields(payloadRaw: Record<string, unknown>): Record<string, string> {
  const result: Record<string, string> = {};
  if (payloadRaw.form && typeof payloadRaw.form === "object") {
    Object.assign(result, flatten(payloadRaw.form, "form"));
  }
  if (payloadRaw.stage_b && typeof payloadRaw.stage_b === "object") {
    Object.assign(result, flatten(payloadRaw.stage_b, "stage_b"));
  }
  return result;
}

function diffFields(
  current: Record<string, string>,
  previous: Record<string, string> | null,
): string[] {
  if (previous === null) return [];
  return Object.keys(current).filter((field) => previous[field] !== current[field]);
}

function FieldTable({
  fields,
  changed,
}: {
  fields: Record<string, string>;
  changed: Set<string>;
}) {
  return (
    <table className="table table--kv table--compact">
      <tbody>
        {Object.entries(fields).map(([field, value]) => (
          <tr key={field}>
            <td>{humanizeFieldPath(field)}</td>
            <td>
              <span className={changed.has(field) ? "diff-changed" : undefined}>{value}</span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function CollapsedSnapshot({
  snapshot,
  changedFields,
}: {
  snapshot: ExternalSnapshotOut;
  changedFields: string[];
}) {
  const [expanded, setExpanded] = useState(false);
  const fields = anketaFields(snapshot.payload_raw as Record<string, unknown>);
  const changedLabels = changedFields.map(humanizeFieldPath);

  return (
    <div className="card" style={{ marginBottom: 6, padding: "8px 12px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
        <p className="muted" style={{ margin: 0, fontSize: 12 }}>
          {formatDateTime(snapshot.received_at)}
          {changedLabels.length > 0 ? (
            <> · изменено: {changedLabels.join(", ")}</>
          ) : (
            <> · первый снимок</>
          )}
        </p>
        <button className="btn btn-small" onClick={() => setExpanded((v) => !v)}>
          {expanded ? "скрыть поля" : "показать все поля"}
        </button>
      </div>
      {expanded && (
        <div style={{ marginTop: 8 }}>
          <FieldTable fields={fields} changed={new Set(changedFields)} />
        </div>
      )}
    </div>
  );
}

// Снимки анкеты по стадиям (ТЗ, «Анкета»): каждый снимок — строка в
// external_snapshots (T04/T10, app/integrations/adapter.py пишет новую при
// каждом изменении содержимого). Компактно (ваше уточнение): по умолчанию
// развёрнут только САМЫЙ СВЕЖИЙ снимок каждой стадии (полная таблица полей),
// остальные — одной строкой с перечнем изменённых полей и кнопкой развернуть,
// иначе десяток снимков стадии Б превращается в стену из одинаковых таблиц.
export function SnapshotHistory({ snapshots }: { snapshots: ExternalSnapshotOut[] }) {
  if (snapshots.length === 0) return <p className="muted">Снимков нет.</p>;

  const byStage = new Map<string, ExternalSnapshotOut[]>();
  for (const snapshot of snapshots) {
    const list = byStage.get(snapshot.stage) ?? [];
    list.push(snapshot);
    byStage.set(snapshot.stage, list);
  }

  return (
    <>
      {[...byStage.entries()].map(([stage, list]) => {
        const latest = list[list.length - 1]!;
        const earlier = list.slice(0, -1);
        const latestFields = anketaFields(latest.payload_raw as Record<string, unknown>);
        const previousFields = earlier.length > 0 ? anketaFields(earlier[earlier.length - 1]!.payload_raw as Record<string, unknown>) : null;
        const latestChanged = new Set(diffFields(latestFields, previousFields));

        return (
          <div key={stage} style={{ marginBottom: 16 }}>
            <h3 style={{ fontSize: 13, color: "var(--color-text-muted)", margin: "8px 0" }}>
              Стадия {stage} {list.length > 1 && <span className="muted">({list.length} снимков)</span>}
            </h3>

            {earlier.map((snapshot, index) => {
              const current = anketaFields(snapshot.payload_raw as Record<string, unknown>);
              const previous = index > 0 ? anketaFields(earlier[index - 1]!.payload_raw as Record<string, unknown>) : null;
              const changed = diffFields(current, previous);
              return <CollapsedSnapshot key={snapshot.id} snapshot={snapshot} changedFields={changed} />;
            })}

            <div className="card">
              <p className="muted" style={{ fontSize: 12 }}>
                Получено {formatDateTime(latest.received_at)} — последний снимок
                {latestChanged.size > 0 && `, изменений: ${latestChanged.size}`}
              </p>
              <FieldTable fields={latestFields} changed={latestChanged} />
            </div>
          </div>
        );
      })}
    </>
  );
}
