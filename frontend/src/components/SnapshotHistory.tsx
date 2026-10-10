import { useState } from "react";
import { flatten } from "../lib/flatten";
import { humanizeFieldPath } from "../lib/labels";
import { formatDateTime } from "../lib/format";
import type { components } from "../api/schema";

type ExternalSnapshotOut = components["schemas"]["ExternalSnapshotOut"];

// Сырой снимок от заказчика — это весь ответ их API (id/status/photos/
// comments/status_history/technical и т.п.). Здесь — только форма и поля
// стадии Б: остальное уже показано в других блоках карточки.
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

function diffFields(current: Record<string, string>, previous: Record<string, string> | null): string[] {
  if (previous === null) return [];
  return Object.keys(current).filter((field) => previous[field] !== current[field]);
}

// T17, часть 2 (ваше замечание «одинаковые снимки не дублировать»): подряд
// идущие снимки с тем же содержимым — одна строка с числом повторов. Сравнение
// по полному payload, а не по полям анкеты: так «тот же ответ заказчика»
// совпадает ровно с тем, что хранит content_hash на бэкенде.
interface SnapshotGroup {
  first: ExternalSnapshotOut;
  last: ExternalSnapshotOut;
  count: number;
}

function groupIdentical(list: ExternalSnapshotOut[]): SnapshotGroup[] {
  const groups: SnapshotGroup[] = [];
  let previousKey: string | null = null;
  for (const snapshot of list) {
    const key = JSON.stringify(snapshot.payload_raw);
    const current = groups[groups.length - 1];
    if (current && key === previousKey) {
      current.last = snapshot;
      current.count += 1;
    } else {
      groups.push({ first: snapshot, last: snapshot, count: 1 });
    }
    previousKey = key;
  }
  return groups;
}

function FieldTable({ fields, changed }: { fields: Record<string, string>; changed: Set<string> }) {
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

function GroupRow({
  group,
  changedFields,
  defaultOpen,
  fields,
}: {
  group: SnapshotGroup;
  changedFields: string[];
  defaultOpen: boolean;
  fields: Record<string, string>;
}) {
  const [expanded, setExpanded] = useState(defaultOpen);
  const changedLabels = changedFields.map(humanizeFieldPath);
  const repeated = group.count > 1 ? ` · повторно ${group.count - 1} раз, последний ${formatDateTime(group.last.received_at)}` : "";

  return (
    <div className="snapshot">
      <div className="row row--between">
        <p className="muted text-xs">
          {formatDateTime(group.first.received_at)}
          {repeated}
          {changedLabels.length > 0 ? <> · изменено: {changedLabels.join(", ")}</> : <> · первый снимок</>}
        </p>
        <button className="btn btn-small" onClick={() => setExpanded((v) => !v)}>
          {expanded ? "скрыть поля" : "показать все поля"}
        </button>
      </div>
      {expanded && (
        <div className="stack">
          <FieldTable fields={fields} changed={new Set(changedFields)} />
        </div>
      )}
    </div>
  );
}

// Снимки анкеты по стадиям (ТЗ, «Анкета»): каждый снимок — строка в
// external_snapshots (app/integrations/adapter.py пишет новую при каждом
// изменении содержимого). Последняя группа каждой стадии развёрнута сразу,
// остальные — одной строкой с перечнем изменённых полей.
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
        const groups = groupIdentical(list);
        const fieldsOf = (group: SnapshotGroup) => anketaFields(group.first.payload_raw as Record<string, unknown>);
        return (
          <div key={stage} className="subsection">
            <h3 className="subhead">
              Стадия {stage === "A" ? "А" : stage === "B" ? "Б" : stage}
              {groups.length > 1 && <span className="muted"> ({groups.length} разных снимков)</span>}
            </h3>
            {groups.map((group, index) => {
              const fields = fieldsOf(group);
              const previous = index > 0 ? fieldsOf(groups[index - 1]!) : null;
              return (
                <GroupRow
                  key={group.first.id}
                  group={group}
                  changedFields={diffFields(fields, previous)}
                  defaultOpen={index === groups.length - 1}
                  fields={fields}
                />
              );
            })}
          </div>
        );
      })}
    </>
  );
}
