import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { ErrorState, LoadingState, PageHeader } from "../components/ui";
import { healthProblems } from "../lib/health";

type SystemStatus = components["schemas"]["SystemStatus"];
type Tone = "ok" | "bad" | "warn" | "mute";

const TONE_ICON: Record<Tone, string> = { ok: "✓", bad: "!", warn: "!", mute: "–" };

// Плитка одного узла системы: статус точкой-иконкой, крупное значение, пояснение.
function Tile({ title, value, hint, tone }: { title: string; value: string; hint?: string; tone: Tone }) {
  return (
    <div className={`sys-tile sys-tile--${tone}`}>
      <div className="sys-tile__head">
        <span className="sys-tile__icon" aria-hidden="true">
          {TONE_ICON[tone]}
        </span>
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

// T23 (по отзыву): сверху — крупный итог, ниже — проблемы карточками с тем,
// что делать, и плитки по узлам системы (правила, БКИ, CRM, конвейер, ФССП,
// реестры) — все цифры видны сразу, без свёрнутой таблицы.
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

  const header = <PageHeader title="Состояние системы" description="Обновляется каждые 30 секунд." />;
  if (query.isLoading) return <section>{header}<LoadingState /></section>;
  if (query.isError || !query.data) {
    return (
      <section>
        {header}
        <ErrorState message="Не удалось загрузить состояние системы." onRetry={() => void query.refetch()} />
      </section>
    );
  }

  const s: SystemStatus = query.data;
  const problems = healthProblems(s);
  const updatedAt = new Date(query.dataUpdatedAt).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const pipelineStuck = s.stuck_applications_over_1h + s.stuck_in_ocr + s.stage_b_reprocessing_loop_applications;

  return (
    <section className="sys">
      {header}

      <div className={problems.length > 0 ? "sys-hero sys-hero--bad" : "sys-hero sys-hero--ok"}>
        <span className="sys-hero__icon" aria-hidden="true">
          {problems.length > 0 ? "!" : "✓"}
        </span>
        <div>
          <p className="sys-hero__title" data-testid="health-summary">
            {problems.length > 0 ? `Есть проблемы: ${problems.length}` : "Всё работает"}
          </p>
          <p className="sys-hero__sub">Проверено в {updatedAt}</p>
        </div>
      </div>

      {problems.length > 0 && (
        <ul className="sys-problems" data-testid="health-problems">
          {problems.map((p) => (
            <li key={p.key} className="sys-problem">
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

      <h2 className="sys-section">Узлы системы</h2>
      <div className="sys-grid">
        <Tile
          title="Правила скоринга"
          value={s.ruleset_active_version != null ? `версия ${s.ruleset_active_version}` : "нет"}
          hint={
            s.ruleset_matches_file === null
              ? "сверка с файлом неизвестна"
              : s.ruleset_matches_file
                ? "совпадает с файлом на сервере"
                : "отличается от файла на сервере"
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
          hint="за последние сутки"
          tone={s.partner_integration_errors_last_24h === 0 ? "ok" : "warn"}
        />
        <Tile
          title="Конвейер заявок"
          value={pipelineStuck === 0 ? "всё движется" : `${pipelineStuck} застряли`}
          hint={`больше часа без движения: ${s.stuck_applications_over_1h} · в распознавании: ${s.stuck_in_ocr} · стадия Б по кругу: ${s.stage_b_reprocessing_loop_applications}`}
          tone={pipelineStuck === 0 ? "ok" : "bad"}
        />
        <Tile
          title="ФССП после решения"
          value={s.fssp_results_after_decision === 0 ? "нет" : String(s.fssp_results_after_decision)}
          hint="долг нашёлся уже после решения по заявке"
          tone={s.fssp_results_after_decision === 0 ? "ok" : "warn"}
        />
        {s.registries.map((r) => (
          <Tile
            key={r.registry}
            title={registryLabel(r.registry)}
            value={r.registry === "mvd_invalid_passports" ? "не используется" : r.stale ? "устарел" : "свежий"}
            hint={r.registry === "mvd_invalid_passports" ? "реестр отменён в 2023 году" : "обновляется планировщиком"}
            tone={r.registry === "mvd_invalid_passports" ? "mute" : r.stale ? "warn" : "ok"}
          />
        ))}
      </div>
    </section>
  );
}

function registryLabel(registry: string): string {
  if (registry === "rosfinmon_sanctions") return "Перечень Росфинмониторинга";
  if (registry === "mvd_invalid_passports") return "Недействительные паспорта МВД";
  return registry;
}
