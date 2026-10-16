import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState, PageHeader, SearchInput, Segmented, Toolbar, matches } from "../components/ui";
import { formatDateTime } from "../lib/format";
import { complianceVerdictLabel, reasonCodeLabel } from "../lib/labels";

// T23 (решение владельца): бывший «Комплаенс» — «Проверка по перечням».
// Слово «комплаенс» людям непонятно; разбирают совпадения admin и operator.
export function CompliancePage() {
  const queryClient = useQueryClient();
  const [openReasonFor, setOpenReasonFor] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [freezeFilter, setFreezeFilter] = useState<"" | "frozen">("");

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

  // Замороженные — сверху: это очередь работы; снятые остаются ниже для истории.
  const items = (query.data?.items ?? [])
    .filter(
      (m) =>
        matches(search, m.external_id, m.client_name) && (freezeFilter === "" || m.freeze_active),
    )
    .sort((a, b) => Number(b.freeze_active) - Number(a.freeze_active));
  const frozenCount = (query.data?.items ?? []).filter((m) => m.freeze_active).length;

  return (
    <section>
      <PageHeader
        title="Проверка по перечням"
        description="Заявители, чьё ФИО совпало с перечнем Росфинмониторинга (террористы и экстремисты, 115-ФЗ). Такому клиенту рассрочку выдавать нельзя, поэтому система его замораживает и ждёт, пока человек разберётся."
      />
      <div className="watchlist-explain">
        <div className="watchlist-explain__title">Как разобрать совпадение</div>
        <ol>
          <li>
            Откройте заявку и сравните данные клиента с записью в перечне: дату и место рождения, паспорт. Совпадение
            ищется по ФИО, поэтому часто это просто однофамилец.
          </li>
          <li>
            <b>Не он</b> — нажмите «Пометить ложным», затем «Снять заморозку» и напишите, почему (например, «другая
            дата рождения»). Клиент снова может получить рассрочку.
          </li>
          <li>
            <b>Он</b> — нажмите «Подтвердить совпадение». Заморозка остаётся, рассрочку не выдаём. Клиенту причину
            не сообщаем.
          </li>
        </ol>
      </div>
      <Toolbar>
        <SearchInput value={search} onChange={setSearch} placeholder="Номер заявки или ФИО" />
        <Segmented
          label="Заморозка"
          value={freezeFilter}
          onChange={setFreezeFilter}
          options={[
            { value: "", label: "Все", count: query.data?.items.length },
            { value: "frozen", label: "Заморожено", count: frozenCount },
          ]}
        />
      </Toolbar>
      {query.isLoading && <LoadingState />}
      {query.isError && <ErrorState message="Не удалось загрузить совпадения." onRetry={() => void query.refetch()} />}
      {query.data && items.length === 0 && (
        <EmptyState
          title={freezeFilter === "frozen" ? "Активных заморозок нет" : "Совпадений нет"}
          hint="Новые совпадения появятся здесь автоматически после стадии А."
        />
      )}
      {items.length > 0 && (
        <div className="match-list">
          {items.map((m) => (
            <article key={m.decision_id} className="match">
              <div>
                <div className="match__title">
                  <Link to={`/applications/${m.application_id}`}>{m.external_id}</Link> · {m.client_name ?? "—"}
                </div>
                <div className="match__meta">
                  {m.reason_code ? reasonCodeLabel(m.reason_code) : "—"} · {formatDateTime(m.decided_at)} ·{" "}
                  <span className={`badge ${m.freeze_active ? "badge-danger" : "badge-neutral"}`}>
                    {m.freeze_active ? "заморожено" : "снято"}
                  </span>{" "}
                  · разбор: {complianceVerdictLabel(m.latest_review_verdict)}
                </div>
              </div>
              <div className="match__actions">
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
                  <button className="btn btn-small btn-primary" onClick={() => setOpenReasonFor(m.decision_id)}>
                    Снять заморозку
                  </button>
                )}
              </div>

              {openReasonFor === m.decision_id && m.client_id && (
                <div className="match__lift">
                  <label className="field">
                    Причина снятия (обязательна)
                    <input value={reason} onChange={(e) => setReason(e.target.value)} />
                  </label>
                  <button
                    className="btn btn-primary"
                    disabled={liftFreezeMutation.isPending || !reason.trim()}
                    onClick={() => submitLiftFreeze(m.client_id!, m.decision_id)}
                  >
                    Подтвердить снятие
                  </button>
                  <button className="btn" onClick={() => setOpenReasonFor(null)}>
                    Отмена
                  </button>
                </div>
              )}
              {openReasonFor === m.decision_id && reasonError && (
                <div className="match__lift">
                  <ErrorState message={reasonError} />
                </div>
              )}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
