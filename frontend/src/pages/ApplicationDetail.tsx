import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { Badge } from "../components/Badge";
import { SnapshotHistory } from "../components/SnapshotHistory";
import { SourceBadge } from "../components/SourceBadge";
import { formatAmount, formatDateTime } from "../lib/format";
import {
  actorTypeLabel,
  applicationStatusLabel,
  documentKindLabel,
  humanizeFieldPath,
  subjectTypeLabel,
} from "../lib/labels";

export function ApplicationDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { employee } = useAuth();
  const queryClient = useQueryClient();
  const [openDocumentId, setOpenDocumentId] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["application", id],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/applications/{application_id}", {
        params: { path: { application_id: id! } },
      });
      if (error) throw error;
      return data;
    },
    enabled: Boolean(id),
  });

  const retryMutation = useMutation({
    mutationFn: async (checkId: string) => {
      const { data, error } = await api.POST(
        "/api/applications/{application_id}/checks/{check_id}/retry",
        { params: { path: { application_id: id!, check_id: checkId } } },
      );
      if (error) throw error;
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["application", id] });
    },
  });

  if (query.isLoading) return <p className="muted">Загрузка…</p>;
  if (query.isError || !query.data) {
    return <p role="alert" className="alert">Не удалось загрузить заявку.</p>;
  }

  const app = query.data;
  const canRetry = employee?.role === "risk_manager";

  return (
    <section>
      <p>
        <Link to="/applications">← к списку заявок</Link>
      </p>
      <header className="card">
        <h1>
          Заявка №{app.number} <span className="muted">(у заказчика {app.external_id})</span>
        </h1>
        <p>
          {app.client_name ?? "—"} · <Badge value={app.status} label={applicationStatusLabel(app.status)} /> ·{" "}
          {formatAmount(app.requested_amount)} ₽ на{" "}
          {app.term_months} мес. · подана {formatDateTime(app.submitted_at)}
        </p>
      </header>

      <h2>Решения</h2>
      {app.decisions.length === 0 && <p className="muted">Решений пока нет.</p>}
      {app.decisions.map((d) => (
        <article key={d.id} className="card">
          <p>
            Стадия {d.stage}: <Badge value={d.outcome} />
            {d.freeze && <span className="badge badge-danger" style={{ marginLeft: 8 }}>заморожено</span>}{" "}
            <span className="muted">{formatDateTime(d.created_at)}</span>
          </p>
          {d.triggered_rules_text.length > 0 && (
            <ul style={{ margin: "8px 0 0", paddingLeft: 20 }}>
              {d.triggered_rules_text.map((text, i) => (
                <li key={i}>{text}</li>
              ))}
            </ul>
          )}
          {(d.approved_amount || d.score) && (
            <p className="muted" style={{ marginTop: 8 }}>
              {d.score && `скор ${d.score}`}
              {d.approved_amount && ` · одобрено ${formatAmount(d.approved_amount)} ₽ на ${d.approved_term} мес.`}
            </p>
          )}
        </article>
      ))}

      {app.manual_overrides.length > 0 && (
        <>
          <h2>Итог оператора</h2>
          <div className="card">
            {app.manual_overrides.map((o) => (
              <p key={o.id}>
                <Badge value={o.from_outcome} /> → <Badge value={o.to_outcome} />{" "}
                <span className="muted">
                  ({o.reason_code}){o.comment && `: ${o.comment}`}
                </span>
              </p>
            ))}
          </div>
        </>
      )}

      <h2>Проверки</h2>
      <table className="table">
        <thead>
          <tr>
            <th>Источник</th>
            <th>Кто</th>
            <th>Статус</th>
            <th>Время</th>
            <th>Ошибка</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {app.checks.map((c) => (
            <tr key={c.id}>
              <td>
                <SourceBadge source={c.source} />
              </td>
              <td className="muted">{subjectTypeLabel(c.subject_type)}</td>
              <td>
                <Badge value={c.status} />
              </td>
              <td className="muted">{formatDateTime(c.started_at)}</td>
              <td className="muted">{c.error_text ?? "—"}</td>
              <td>
                {c.can_retry && canRetry && (
                  <button
                    className="btn btn-small"
                    disabled={retryMutation.isPending}
                    onClick={() => retryMutation.mutate(c.id)}
                  >
                    Повторить
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {retryMutation.isError && <p role="alert" className="alert">Не удалось повторить проверку.</p>}

      {app.ocr_result && (
        <>
          <h2>Распознавание</h2>
          <p className="muted">
            Провайдер {app.ocr_result.provider}, мин. уверенность {app.ocr_result.min_confidence ?? "—"} ·{" "}
            {formatDateTime(app.ocr_result.created_at)}
          </p>
          <table className="table table--compact">
            <thead>
              <tr>
                <th>Поле</th>
                <th>Значение</th>
                <th>Уверенность</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries((app.ocr_result.fields.fields as Record<string, unknown>) ?? {}).map(
                ([field, value]) => {
                  const v = value as { value?: unknown; confidence?: number };
                  return (
                    <tr key={field}>
                      <td>{humanizeFieldPath(field)}</td>
                      <td>{String(v.value ?? "—")}</td>
                      <td className="muted">{v.confidence ?? "—"}</td>
                    </tr>
                  );
                },
              )}
            </tbody>
          </table>
        </>
      )}

      {app.external_snapshots.length > 0 && (
        <>
          <h2>Снимки анкеты</h2>
          <SnapshotHistory snapshots={app.external_snapshots} />
        </>
      )}

      <h2>Документы</h2>
      {app.documents.length === 0 && <p className="muted">Документов нет.</p>}
      <ul style={{ listStyle: "none", padding: 0 }}>
        {app.documents.map((doc) => (
          <li key={doc.id} className="card">
            {documentKindLabel(doc.kind)} <span className="muted">· {formatDateTime(doc.uploaded_at)}</span>{" "}
            <button className="btn btn-small" onClick={() => setOpenDocumentId(doc.id)}>
              показать
            </button>
            {openDocumentId === doc.id && (
              <div style={{ marginTop: 8 }}>
                <img
                  src={`/api/applications/${app.id}/documents/${doc.id}`}
                  alt={doc.kind}
                  style={{ maxWidth: 320, display: "block", borderRadius: 6, border: "1px solid var(--color-border)" }}
                />
              </div>
            )}
          </li>
        ))}
      </ul>

      <h2>История</h2>
      <table className="table">
        <thead>
          <tr>
            <th>Из</th>
            <th>В</th>
            <th>Кто</th>
            <th>Когда</th>
          </tr>
        </thead>
        <tbody>
          {app.status_history.map((h, i) => (
            <tr key={i}>
              <td>
                <Badge value={h.from_status} label={h.from_status ? applicationStatusLabel(h.from_status) : undefined} />
              </td>
              <td>
                <Badge value={h.to_status} label={applicationStatusLabel(h.to_status)} />
              </td>
              <td className="muted">{actorTypeLabel(h.actor_type)}</td>
              <td className="muted">{formatDateTime(h.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
