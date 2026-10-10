import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { healthProblems } from "../lib/health";

type SystemStatus = components["schemas"]["SystemStatus"];

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <tr>
      <td>{label}</td>
      <td>{value}</td>
    </tr>
  );
}

// T20: сверху — одна строка-итог («Всё работает» / «Есть проблемы: N») и
// список проблем человеческим языком с тем, что делать. Счётчики, версии и
// реестры — в свёрнутых «Подробностях».
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

  const s: SystemStatus = query.data;
  const problems = healthProblems(s);

  return (
    <section>
      <h1>Состояние системы</h1>
      <p
        className={problems.length > 0 ? "health-summary health-summary--bad" : "health-summary"}
        data-testid="health-summary"
      >
        {problems.length > 0 ? `Есть проблемы: ${problems.length}` : "Всё работает"}
      </p>
      {problems.length > 0 && (
        <ul className="problem-list" data-testid="health-problems">
          {problems.map((p) => (
            <li key={p.key}>
              <div className="problem-what">{p.what}</div>
              <div className="problem-todo">{p.todo}</div>
            </li>
          ))}
        </ul>
      )}

      <details className="disclosure">
        <summary>Подробности</summary>
        <table className="table table--kv">
          <tbody>
            <DetailRow
              label="Версия правил"
              value={s.ruleset_active_version != null ? String(s.ruleset_active_version) : "нет активной"}
            />
            <DetailRow
              label="Версия правил совпадает с файлом"
              value={s.ruleset_matches_file === null ? "неизвестно" : s.ruleset_matches_file ? "да" : "нет"}
            />
            <DetailRow label="БКИ подключено" value={s.bki_configured ? "да" : "нет"} />
            <DetailRow label="Платных запросов в БКИ за месяц" value={String(s.bki_paid_requests_this_month)} />
            <DetailRow label="ФССП найдено после решения" value={String(s.fssp_results_after_decision)} />
            <DetailRow label="Ошибок интеграции с заказчиком за сутки" value={String(s.partner_integration_errors_last_24h)} />
            <DetailRow label="Заявок без изменений больше часа" value={String(s.stuck_applications_over_1h)} />
            <DetailRow label="Заявок, зависших в распознавании паспорта" value={String(s.stuck_in_ocr)} />
            <DetailRow
              label="Заявок с петлёй повторной обработки стадии Б за час"
              value={String(s.stage_b_reprocessing_loop_applications)}
            />
            {s.registries.map((r) => (
              <DetailRow key={r.registry} label={`Реестр: ${registryLabel(r.registry)}`} value={r.stale ? "устарел" : "свежий"} />
            ))}
          </tbody>
        </table>
      </details>
    </section>
  );
}

function registryLabel(registry: string): string {
  if (registry === "rosfinmon_sanctions") return "перечень Росфинмониторинга";
  if (registry === "mvd_invalid_passports") return "недействительные паспорта МВД";
  return registry;
}
