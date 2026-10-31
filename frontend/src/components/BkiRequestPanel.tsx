import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { formatDateTime } from "../lib/format";

type ReportType = components["schemas"]["BkiReportTypeOut"];
type Order = components["schemas"]["BkiOrderOut"];

// Запрос в БКИ платный — только по кнопке (решение владельца). Стадия Б
// ждёт в awaiting_bki; сотрудник выбирает вид отчёта, дальше — фоновая задача.
export function BkiRequestPanel({
  applicationId,
  types,
  order,
  canOperate,
}: {
  applicationId: string;
  types: ReportType[];
  order: Order | null | undefined;
  canOperate: boolean;
}) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: async (reportType: string) => {
      const { data, error } = await api.POST("/api/applications/{application_id}/bki-request", {
        params: { path: { application_id: applicationId } },
        body: { report_type: reportType },
      });
      if (error) throw error;
      return data;
    },
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ["application", applicationId] }),
  });

  const pending = order?.pending ? order : null;

  return (
    <section className="mc bki-request" data-testid="bki-request">
      <div className="mc__head">
        <h3 className="mc__title">Кредитная история</h3>
        {!pending && <span className="action-tag">требуется действие</span>}
      </div>

      {pending ? (
        <p className="bki-request__status" role="status">
          Заказан {pending.label.toLowerCase()} отчёт
          {pending.employee_name ? ` · ${pending.employee_name}` : ""} · {formatDateTime(pending.created_at)}. Ждём
          ответа бюро — решение придёт само.
        </p>
      ) : types.length === 0 ? (
        <p className="alert" role="alert">
          В действующих правилах нет видов отчёта БКИ (bki.report_types) — администратору: «Скоринг» → принять
          версию из файла.
        </p>
      ) : canOperate ? (
        <>
          <div className="bki-request__buttons">
            {types.map((t) => (
              <button
                key={t.code}
                type="button"
                className={t.allows_approval ? "btn btn-primary" : "btn"}
                disabled={mutation.isPending}
                onClick={() => mutation.mutate(t.code)}
              >
                {t.label} отчёт
              </button>
            ))}
          </div>
          <p className="muted bki-request__hint">
            {types
              .map((t) => `${t.label} — ${t.allows_approval ? "с кредитами и просрочками, дороже" : "без кредитов, одобрить по нему нельзя"}`)
              .join(" · ")}
          </p>
        </>
      ) : (
        <p className="muted">Запрос в БКИ делает оператор или администратор.</p>
      )}

      {mutation.isError && (
        <p className="alert" role="alert">
          {(mutation.error as { detail?: string })?.detail ?? "Не удалось заказать отчёт"}
        </p>
      )}
    </section>
  );
}
