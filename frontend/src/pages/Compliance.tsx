import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState, PageHeader, SearchInput, Segmented, Toolbar, matches } from "../components/ui";
import { formatDateTime } from "../lib/format";
import { complianceVerdictLabel } from "../lib/labels";

// T23 (решение владельца): бывший «Комплаенс» — «Проверка по перечням».
// Слово «комплаенс» людям непонятно; разбирают совпадения admin и operator.
const PAGE = 20;

export function CompliancePage() {
  const queryClient = useQueryClient();
  const [openReasonFor, setOpenReasonFor] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  // T23 (по отзыву): по умолчанию — только то, что ждёт разбора; разобранные
  // и вся история — по переключателю. Длинный список — порциями.
  const [freezeFilter, setFreezeFilter] = useState<"frozen" | "done" | "">("frozen");
  const [shown, setShown] = useState(PAGE);

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

  // T23: раньше ошибка сервера (например, 409 «не найдена запись перечня»)
  // тихо проглатывалась — казалось, что кнопки не работают. Теперь итог и
  // ошибка видны в строке совпадения; «Не он» сразу открывает причину снятия.
  const [reviewError, setReviewError] = useState<{ decisionId: string; message: string } | null>(null);
  const reviewMutation = useMutation({
    mutationFn: async (input: { decisionId: string; verdict: "false_positive" | "confirmed"; openLift: boolean }) => {
      const { error } = await api.POST("/api/compliance/decisions/{decision_id}/mark-false-positive", {
        params: { path: { decision_id: input.decisionId } },
        body: { verdict: input.verdict },
      });
      if (error) {
        throw new Error(typeof error.detail === "string" ? error.detail : "не удалось сохранить разбор");
      }
    },
    onMutate: () => setReviewError(null),
    onSuccess: (_, input) => {
      if (input.openLift) {
        setReason("");
        setReasonError(null);
        setOpenReasonFor(input.decisionId);
      }
      void queryClient.invalidateQueries({ queryKey: ["compliance-matches"] });
    },
    onError: (error: Error, input) => setReviewError({ decisionId: input.decisionId, message: error.message }),
  });
  const pendingFor = reviewMutation.isPending ? reviewMutation.variables?.decisionId : null;

  function submitLiftFreeze(clientId: string, decisionId: string) {
    setReasonError(null);
    liftFreezeMutation.mutate({ clientId, decisionId, reason });
  }

  // Замороженные — сверху: это очередь работы; снятые остаются ниже для истории.
  const all = query.data?.items ?? [];
  const items = all
    .filter(
      (m) =>
        matches(search, m.external_id, m.client_name) &&
        (freezeFilter === "" || (freezeFilter === "frozen" ? m.freeze_active : !m.freeze_active)),
    )
    .sort((a, b) => Number(b.freeze_active) - Number(a.freeze_active));
  const frozenCount = all.filter((m) => m.freeze_active).length;
  const visible = items.slice(0, shown);

  return (
    <section>
      <PageHeader
        title="Проверка по перечням"
        description="ФИО заявителя совпало с перечнем Росфинмониторинга (115-ФЗ). Пока совпадение не разобрано, клиент заморожен: рассрочку ему не выдаём."
      />
      <details className="explain-fold">
        <summary>Как разобрать совпадение</summary>
        <ol>
          <li>Откройте заявку и сравните клиента с записью перечня: дату и место рождения, паспорт. Чаще всего это однофамилец.</li>
          <li>
            <b>Не он</b> — укажите причину («другая дата рождения») и подтвердите снятие: клиент снова может получить
            рассрочку.
          </li>
          <li>
            <b>Он</b> — заморозка остаётся, рассрочку не выдаём, клиенту причину не сообщаем.
          </li>
        </ol>
      </details>
      <Toolbar>
        <SearchInput
          value={search}
          onChange={(v) => {
            setSearch(v);
            setShown(PAGE);
          }}
          placeholder="Номер заявки или ФИО"
        />
        <Segmented
          label="Заморозка"
          value={freezeFilter}
          onChange={(v) => {
            setFreezeFilter(v);
            setShown(PAGE);
          }}
          options={[
            { value: "frozen", label: "Ждут разбора", count: frozenCount },
            { value: "done", label: "Разобраны", count: all.length - frozenCount },
            { value: "", label: "Все", count: all.length },
          ]}
        />
      </Toolbar>
      {query.isLoading && <LoadingState />}
      {query.isError && <ErrorState message="Не удалось загрузить совпадения." onRetry={() => void query.refetch()} />}
      {query.data && items.length === 0 && (
        <EmptyState
          title={freezeFilter === "frozen" ? "Разбирать нечего — заморозок нет" : "Совпадений нет"}
          hint="Новые совпадения появятся здесь автоматически после стадии А."
        />
      )}
      {visible.length > 0 && (
        <div className="match-list">
          {visible.map((m) => (
            <article key={m.decision_id} className={m.freeze_active ? "match match--frozen" : "match"}>
              <span className="match__status">
                <span className={m.freeze_active ? "badge badge-danger" : "badge badge-neutral"}>
                  {m.freeze_active ? "заморожено" : "снято"}
                </span>
              </span>
              <div className="match__who">
                <Link to={`/applications/${m.application_id}`}>{m.external_id}</Link>
                <span className="match__name">{m.client_name ?? "—"}</span>
              </div>
              <div className="match__meta">
                <span>{formatDateTime(m.decided_at)}</span>
                <span>разбор: {complianceVerdictLabel(m.latest_review_verdict)}</span>
              </div>
              <div className="match__actions">
                <button
                  className={
                    m.latest_review_verdict === "false_positive" ? "btn btn-small btn--chosen" : "btn btn-small"
                  }
                  aria-label="Не он — пометить ложным"
                  title="Не он — пометить совпадение ложным"
                  aria-pressed={m.latest_review_verdict === "false_positive"}
                  disabled={reviewMutation.isPending}
                  onClick={() =>
                    reviewMutation.mutate({
                      decisionId: m.decision_id, verdict: "false_positive", openLift: m.freeze_active,
                    })
                  }
                >
                  {pendingFor === m.decision_id && reviewMutation.variables?.verdict === "false_positive"
                    ? "Сохраняю…"
                    : "Не он"}
                </button>
                <button
                  className={m.latest_review_verdict === "confirmed" ? "btn btn-small btn--chosen" : "btn btn-small"}
                  aria-label="Он — подтвердить совпадение"
                  title="Он — подтвердить совпадение"
                  aria-pressed={m.latest_review_verdict === "confirmed"}
                  disabled={reviewMutation.isPending}
                  onClick={() =>
                    reviewMutation.mutate({ decisionId: m.decision_id, verdict: "confirmed", openLift: false })
                  }
                >
                  {pendingFor === m.decision_id && reviewMutation.variables?.verdict === "confirmed"
                    ? "Сохраняю…"
                    : "Он"}
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
              {reviewError?.decisionId === m.decision_id && (
                <div className="match__lift" role="alert">
                  <ErrorState message={`Не сохранилось: ${reviewError.message}.`} />
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
      {items.length > shown && (
        <button type="button" className="btn match-more" onClick={() => setShown((n) => n + PAGE)}>
          Показать ещё {Math.min(PAGE, items.length - shown)} из {items.length - shown}
        </button>
      )}
    </section>
  );
}
