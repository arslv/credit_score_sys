import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { components } from "../api/schema";

type ReportType = components["schemas"]["BkiReportTypeOut"];
type Order = components["schemas"]["BkiOrderOut"];

// Запрос в БКИ платный — только по кнопке (решение владельца). Кнопки — прямо
// в плитке «Кредитная история» блока «Проверки» (16.11.2026): без отдельной
// карточки и пояснений — итог отчёта уже написан в плитке над кнопками.
export function BkiRequestPanel({
  applicationId,
  types,
  order,
  canOperate,
  retry = false,
}: {
  applicationId: string;
  types: ReportType[];
  order: Order | null | undefined;
  canOperate: boolean;
  /** Отчёт уже был (не получен, без балла…) — запрос повторный. */
  retry?: boolean;
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

  const pending = order?.pending && !retry ? order : null;

  if (pending) {
    return (
      <p className="bki-act bki-act__pending" data-testid="bki-request" role="status">
        <span className="bki-act__spinner" aria-hidden="true" />
        Заказан {pending.label.toLowerCase()} отчёт — ждём ответа бюро
      </p>
    );
  }
  if (!canOperate) return null;
  if (types.length === 0) {
    return (
      <p className="alert" role="alert" data-testid="bki-request">
        В правилах нет видов отчёта БКИ — администратору: «Скоринг» → принять версию из файла.
      </p>
    );
  }
  return (
    <div className="bki-act" data-testid="bki-request">
      <span className="bki-act__label">{retry ? "Запросить ещё раз" : "Запросить отчёт"}</span>
      <div className="bki-act__buttons">
        {/* Первый вид — основной; остальные дороже и нужны редко — не выделены. */}
        {types.map((t, i) => (
          <button
            key={t.code}
            type="button"
            className={i === 0 ? "btn btn-primary btn-small" : "btn btn-small"}
            disabled={mutation.isPending}
            onClick={() => mutation.mutate(t.code)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {mutation.isError && (
        <p className="alert" role="alert">
          {(mutation.error as { detail?: string })?.detail ?? "Не удалось заказать отчёт"}
        </p>
      )}
    </div>
  );
}
