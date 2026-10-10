import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { formatDateTime } from "../lib/format";

export function CompliancePage() {
  const queryClient = useQueryClient();
  const [openReasonFor, setOpenReasonFor] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["compliance-matches"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/compliance/matches", {
        params: { query: { page: 1, page_size: 100 } },
      });
      if (error) throw error;
      return data;
    },
  });

  const liftFreezeMutation = useMutation({
    mutationFn: async (input: { clientId: string; decisionId: string; reason: string }) => {
      const { error } = await api.POST("/api/compliance/clients/{client_id}/lift-freeze", {
        params: { path: { client_id: input.clientId } },
        body: { decision_id: input.decisionId, reason: input.reason },
      });
      if (error) throw new Error(typeof error.detail === "string" ? error.detail : "не удалось снять заморозку");
    },
    onSuccess: () => {
      setOpenReasonFor(null);
      setReason("");
      setReasonError(null);
      void queryClient.invalidateQueries({ queryKey: ["compliance-matches"] });
    },
    onError: (error: Error) => setReasonError(error.message),
  });

  const reviewMutation = useMutation({
    mutationFn: async (input: { decisionId: string; verdict: "false_positive" | "confirmed" }) => {
      const { error } = await api.POST("/api/compliance/decisions/{decision_id}/mark-false-positive", {
        params: { path: { decision_id: input.decisionId } },
        body: { verdict: input.verdict },
      });
      if (error) throw error;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["compliance-matches"] }),
  });

  function submitLiftFreeze(clientId: string, decisionId: string) {
    setReasonError(null);
    liftFreezeMutation.mutate({ clientId, decisionId, reason });
  }

  if (query.isLoading) return <p className="muted">Загрузка…</p>;
  if (query.isError || !query.data) return <p role="alert" className="alert">Не удалось загрузить совпадения.</p>;

  return (
    <section>
      <h1>Комплаенс — совпадения по перечням</h1>
      {query.data.items.length === 0 && <p className="muted">Совпадений с активной заморозкой нет.</p>}
      {query.data.items.map((m) => (
        <article key={m.decision_id} className="card">
          <p>
            <Link to={`/applications/${m.application_id}`}>{m.external_id}</Link> · {m.client_name ?? "—"} ·{" "}
            <span className="muted">{formatDateTime(m.decided_at)}</span>
          </p>
          <p className="muted">
            Причина: {m.reason_code ?? "—"} ·{" "}
            <span className={`badge ${m.freeze_active ? "badge-danger" : "badge-neutral"}`}>
              {m.freeze_active ? "заморожено" : "снято"}
            </span>
            {m.latest_review_verdict && ` · разбор: ${m.latest_review_verdict}`}
          </p>

          <div className="row stack">
            <button
              className="btn btn-small"
              disabled={reviewMutation.isPending}
              onClick={() => reviewMutation.mutate({ decisionId: m.decision_id, verdict: "false_positive" })}
            >
              Пометить ложным
            </button>
            <button
              className="btn btn-small"
              disabled={reviewMutation.isPending}
              onClick={() => reviewMutation.mutate({ decisionId: m.decision_id, verdict: "confirmed" })}
            >
              Подтвердить совпадение
            </button>
            {m.freeze_active && m.client_id && (
              <button className="btn btn-small" onClick={() => setOpenReasonFor(m.decision_id)}>
                Снять заморозку
              </button>
            )}
          </div>

          {openReasonFor === m.decision_id && m.client_id && (
            <div className="stack">
              <label className="field">
                Причина снятия (обязательна)
                <input value={reason} onChange={(e) => setReason(e.target.value)} />
              </label>
              {reasonError && <p role="alert" className="alert">{reasonError}</p>}
              <button
                className="btn btn-primary btn-small stack"
                disabled={liftFreezeMutation.isPending || !reason.trim()}
                onClick={() => submitLiftFreeze(m.client_id!, m.decision_id)}
              >
                Подтвердить снятие
              </button>
            </div>
          )}
        </article>
      ))}
    </section>
  );
}
