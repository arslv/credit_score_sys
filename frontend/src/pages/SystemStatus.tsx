import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

function StatusRow({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <tr>
      <td>{label}</td>
      <td>
        <span className={warn ? "badge badge-danger" : "badge badge-neutral"}>{value}</span>
      </td>
    </tr>
  );
}

export function SystemStatusPage() {
  const query = useQuery({
    queryKey: ["system-status"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/system/status");
      if (error) throw error;
      return data;
    },
    refetchInterval: 30_000,
  });

  if (query.isLoading) return <p className="muted">Загрузка…</p>;
  if (query.isError || !query.data) return <p role="alert" className="alert">Не удалось загрузить состояние системы.</p>;

  const s = query.data;

  return (
    <section>
      <h1>Состояние системы</h1>
      <p>
        <span className={`badge ${s.status === "degraded" ? "badge-danger" : "badge-success"}`}>
          {s.status === "degraded" ? "деградация" : "в порядке"}
        </span>
      </p>
      <table className="table table--kv">
        <tbody>
          <StatusRow
            label="Заявок с петлёй повторной обработки стадии Б за час"
            value={String(s.stage_b_reprocessing_loop_applications)}
            warn={s.stage_b_reprocessing_loop_applications > 0}
          />
          <StatusRow
            label="Версия правил"
            value={s.ruleset_active_version != null ? String(s.ruleset_active_version) : "нет активной"}
            warn={s.ruleset_active_version == null}
          />
          <StatusRow
            label="Совпадает с файлом"
            value={s.ruleset_matches_file === null ? "неизвестно" : s.ruleset_matches_file ? "да" : "нет"}
            warn={s.ruleset_matches_file === false}
          />
          <StatusRow
            label="БКИ подключено"
            value={s.bki_configured ? "да" : "нет"}
            warn={!s.bki_configured}
          />
          <StatusRow label="Платных запросов в БКИ за месяц" value={String(s.bki_paid_requests_this_month)} />
          <StatusRow
            label="ФССП найдено после решения (пересмотреть вручную)"
            value={String(s.fssp_results_after_decision)}
            warn={s.fssp_results_after_decision > 0}
          />
          <StatusRow
            label="Ошибок интеграции за сутки"
            value={String(s.partner_integration_errors_last_24h)}
            warn={s.partner_integration_errors_last_24h > 0}
          />
          <StatusRow
            label="Заявок без изменений больше часа"
            value={String(s.stuck_applications_over_1h)}
            warn={s.stuck_applications_over_1h > 0}
          />
          <StatusRow
            label="Заявок, зависших в распознавании паспорта"
            value={String(s.stuck_in_ocr)}
            warn={s.stuck_in_ocr > 0}
          />
        </tbody>
      </table>

      <h2>Реестры</h2>
      <table className="table">
        <thead>
          <tr>
            <th>Реестр</th>
            <th>Состояние</th>
          </tr>
        </thead>
        <tbody>
          {s.registries.map((r) => (
            <tr key={r.registry}>
              <td>{r.registry}</td>
              <td>
                <span className={`badge ${r.stale ? "badge-danger" : "badge-success"}`}>
                  {r.stale ? "устарел" : "свежий"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
