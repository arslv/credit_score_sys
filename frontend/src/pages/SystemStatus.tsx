import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { StatusIcon, type Tone } from "../components/ChecksOverview";
import { ErrorState, LoadingState } from "../components/ui";
import { healthProblems } from "../lib/health";

type SystemStatus = components["schemas"]["SystemStatus"];

// Плитка одного узла системы — как группа в «Проверках» карточки заявки:
// значок итога и название, крупное значение, одна короткая строка пояснения.
function Tile({ title, value, hint, tone }: { title: string; value: string; hint?: string; tone: Tone }) {
  return (
    <div className={`sys-tile sys-tile--${tone}`}>
      <div className="sys-tile__head">
        <StatusIcon tone={tone} />
        <span className="sys-tile__title">{title}</span>
      </div>
      <div className="sys-tile__value">{value}</div>
      {hint && <div className="sys-tile__hint">{hint}</div>}
    </div>
  );
}

// Куда идти, чтобы разобраться с проблемой (если это делается в админке).
const PROBLEM_LINKS: Record<string, { to: string; label: string }> = {
  "ruleset-missing": { to: "/rulesets", label: "К правилам" },
  "ruleset-mismatch": { to: "/rulesets", label: "К правилам" },
  stuck: { to: "/applications", label: "К заявкам" },
  ocr: { to: "/applications", label: "К заявкам" },
  "fssp-late": { to: "/applications", label: "К заявкам" },
};

// Вид от 16.11.2026 — в стиле карточки заявки: шапка страницы с итогом
// справа, проблемы — карточками с красной кромкой, узлы — плитками с
// короткими пояснениями.
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

  const updatedAt = query.dataUpdatedAt
    ? new Date(query.dataUpdatedAt).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : null;
  const s: SystemStatus | undefined = query.data;
  const problems = s ? healthProblems(s) : [];

  return (
    <section className="pg sys">
      <header className="pg-head">
        <div>
          <h1>Состояние системы</h1>
          <p className="pg-head__sub">
            Обновляется каждые 30 секунд{updatedAt ? ` · проверено в ${updatedAt}` : ""}
          </p>
        </div>
        {s && (
          <div className="pg-stats">
            <div className={problems.length > 0 ? "pg-stat pg-stat--bad" : "pg-stat pg-stat--ok"}>
              <span className="pg-stat__value sys-summary" data-testid="health-summary">
                {problems.length > 0 ? `Есть проблемы: ${problems.length}` : "Всё работает"}
              </span>
              <span className="pg-stat__label">{problems.length > 0 ? "нужно внимание" : "проблем нет"}</span>
            </div>
          </div>
        )}
      </header>

      {query.isLoading && <LoadingState />}
      {(query.isError || (!query.isLoading && !s)) && (
        <ErrorState message="Не удалось загрузить состояние системы." onRetry={() => void query.refetch()} />
      )}

      {s && problems.length > 0 && (
        <ul className="sys-problems" data-testid="health-problems">
          {problems.map((p) => (
            <li key={p.key} className="sys-problem">
              <StatusIcon tone="bad" />
              <div className="sys-problem__text">
                <div className="sys-problem__what">{p.what}</div>
                <div className="sys-problem__todo">{p.todo}</div>
              </div>
              {PROBLEM_LINKS[p.key] && (
                <Link className="btn btn-small" to={PROBLEM_LINKS[p.key]!.to}>
                  {PROBLEM_LINKS[p.key]!.label}
                </Link>
              )}
            </li>
          ))}
        </ul>
      )}

      {s && <Nodes s={s} />}
    </section>
  );
}

function Nodes({ s }: { s: SystemStatus }) {
  const pipelineStuck = s.stuck_applications_over_1h + s.stuck_in_ocr + s.stage_b_reprocessing_loop_applications;
  return (
    <>
      <h2 className="sys-section">Узлы системы</h2>
      <div className="sys-grid">
        <Tile
          title="Правила скоринга"
          value={s.ruleset_active_version != null ? `версия ${s.ruleset_active_version}` : "нет"}
          hint={
            s.ruleset_matches_file === null
              ? "сверка с файлом неизвестна"
              : s.ruleset_matches_file
                ? "совпадает с файлом"
                : "отличается от файла"
          }
          tone={s.ruleset_active_version == null ? "bad" : s.ruleset_matches_file === false ? "warn" : "ok"}
        />
        <Tile
          title="БКИ"
          value={s.bki_configured ? "подключено" : "не подключено"}
          hint={`платных запросов за месяц: ${s.bki_paid_requests_this_month}`}
          tone={s.bki_configured ? "ok" : "bad"}
        />
        <Tile
          title="Обмен с CRM"
          value={s.partner_integration_errors_last_24h === 0 ? "без ошибок" : `${s.partner_integration_errors_last_24h} ошибок`}
          hint="за сутки"
          tone={s.partner_integration_errors_last_24h === 0 ? "ok" : "warn"}
        />
        <Tile
          title="Конвейер заявок"
          value={pipelineStuck === 0 ? "всё движется" : `${pipelineStuck} застряли`}
          hint={
            pipelineStuck === 0
              ? undefined
              : [
                  s.stuck_applications_over_1h ? `без движения: ${s.stuck_applications_over_1h}` : null,
                  s.stuck_in_ocr ? `в распознавании: ${s.stuck_in_ocr}` : null,
                  s.stage_b_reprocessing_loop_applications ? `по кругу: ${s.stage_b_reprocessing_loop_applications}` : null,
                ]
                  .filter(Boolean)
                  .join(" · ")
          }
          tone={pipelineStuck === 0 ? "ok" : "bad"}
        />
        <Tile
          title="ФССП после решения"
          value={s.fssp_results_after_decision === 0 ? "нет" : String(s.fssp_results_after_decision)}
          hint="долг найден после решения"
          tone={s.fssp_results_after_decision === 0 ? "ok" : "warn"}
        />
        {s.registries.map((r) =>
          // Реестр МВД отменён в 2023 — недействительный паспорт проверяет бюро.
          r.registry === "mvd_invalid_passports" ? (
            <Tile
              key={r.registry}
              title="Недействительные паспорта"
              value={s.bki_configured ? "через БКИ" : "не проверяются"}
              hint={s.bki_configured ? "в «Подробном» отчёте" : "подключите БКИ"}
              tone={s.bki_configured ? "ok" : "warn"}
            />
          ) : (
            <Tile
              key={r.registry}
              title={registryLabel(r.registry)}
              value={r.stale ? "устарел" : "свежий"}
              hint="обновляется сам"
              tone={r.stale ? "warn" : "ok"}
            />
          ),
        )}
      </div>
    </>
  );
}

function registryLabel(registry: string): string {
  if (registry === "rosfinmon_sanctions") return "Перечень Росфинмониторинга";
  return registry;
}
