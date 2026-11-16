import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { StatusIcon, type Tone } from "../components/ChecksOverview";
import { ErrorState, LoadingState } from "../components/ui";
import { formatDate } from "../lib/format";
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
  return (
    <>
      <h2 className="sys-section">Узлы системы</h2>
      <div className="sys-grid">
        {/* Коротко (решение владельца 16.11.2026): работает или нет, без
            пояснений; у перечня — дата последней загрузки. */}
        <Tile
          title="Правила скоринга"
          value={s.ruleset_active_version != null ? `версия ${s.ruleset_active_version}` : "нет"}
          tone={s.ruleset_active_version == null ? "bad" : s.ruleset_matches_file === false ? "warn" : "ok"}
        />
        <Tile title="БКИ" value={s.bki_configured ? "активно" : "не активно"} tone={s.bki_configured ? "ok" : "bad"} />
        <Tile
          title="Обмен с CRM"
          value={s.partner_configured ? "активно" : "не активно"}
          hint={
            s.partner_configured && s.partner_integration_errors_last_24h > 0
              ? `ошибок за сутки: ${s.partner_integration_errors_last_24h}`
              : undefined
          }
          tone={!s.partner_configured ? "bad" : s.partner_integration_errors_last_24h > 0 ? "warn" : "ok"}
        />
        {s.registries
          // Реестр МВД отменён в 2023 — недействительный паспорт проверяет бюро.
          .filter((r) => r.registry !== "mvd_invalid_passports")
          .map((r) => {
            const loaded = r.last_loaded_at ? new Date(r.last_loaded_at) : null;
            const today = loaded != null && loaded.toDateString() === new Date().toDateString();
            return (
              <Tile
                key={r.registry}
                title={registryLabel(r.registry)}
                value={loaded ? formatDate(r.last_loaded_at) : "не загружался"}
                tone={today ? "ok" : r.stale || !loaded ? "warn" : "none"}
              />
            );
          })}
      </div>
    </>
  );
}

function registryLabel(registry: string): string {
  if (registry === "rosfinmon_sanctions") return "Перечень Росфинмониторинга";
  return registry;
}
