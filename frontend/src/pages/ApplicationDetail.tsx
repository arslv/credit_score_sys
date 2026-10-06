import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { Badge } from "../components/Badge";
import { FsspPanel } from "../components/FsspPanel";
import { SnapshotHistory } from "../components/SnapshotHistory";
import { SourceBadge } from "../components/SourceBadge";
import { Term } from "../components/Term";
import { clientRows, dealRows, latestStageBPayload } from "../lib/card";
import { formatAmount, formatDate, formatDateTime } from "../lib/format";
import {
  actorTypeLabel,
  applicationStatusLabel,
  checkResultLabel,
  decisionOutcomeLabel,
  documentKindLabel,
  humanizeFieldPath,
  reasonCodeLabel,
  stageLabel,
  subjectTypeLabel,
  tariffLabel,
} from "../lib/labels";

// Числа в карточке — без копеек (780, а не 780.00): баллы и пороги целые.
function wholeNumber(value: number | string): string {
  return String(Math.round(Number(value)));
}

function decisionVerdictClass(outcome: string | undefined): string {
  if (outcome === "approve") return "verdict--approve";
  if (outcome === "reject") return "verdict--reject";
  if (outcome === "manual_review") return "verdict--gray";
  return "verdict--pending";
}

// Сравнения решения с порогами ИЗ ВЕРСИИ ПРАВИЛ ЭТОГО РЕШЕНИЯ (бэкенд отдаёт
// пороги версии decision.ruleset_version_id, не текущей). Здесь — только
// арифметика сравнения, никаких чисел из конфига в коде фронта.
function bkiComparison(decision: {
  score: string | null;
  score_auto_approve_above: number | null;
  score_auto_reject_below: number | null;
}): string | null {
  if (decision.score == null || decision.score_auto_approve_above == null || decision.score_auto_reject_below == null) {
    return null;
  }
  const score = Number(decision.score);
  const approve = decision.score_auto_approve_above;
  const reject = decision.score_auto_reject_below;
  if (score >= approve) return `Балл БКИ ${wholeNumber(score)} — не ниже порога одобрения ${wholeNumber(approve)}`;
  if (score < reject) return `Балл БКИ ${wholeNumber(score)} — ниже порога отказа ${wholeNumber(reject)}`;
  return `Балл БКИ ${wholeNumber(score)} — между порогами ${wholeNumber(reject)} и ${wholeNumber(approve)}: серая зона`;
}

function ptiComparison(decision: { pti: number | null; pti_max: number | null }): string | null {
  if (decision.pti == null || decision.pti_max == null) return null;
  const pti = Math.round(decision.pti * 100);
  const max = Math.round(decision.pti_max * 100);
  return decision.pti > decision.pti_max
    ? `ПДН ${pti}% — выше порога ${max}%`
    : `ПДН ${pti}% — в пределах порога ${max}%`;
}

export function ApplicationDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { employee } = useAuth();
  const queryClient = useQueryClient();
  const [openDocumentId, setOpenDocumentId] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["application", id],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/applications/{application_id}", {
        params: { path: { application_id: id! } },
      });
      if (error) throw error;
      return data;
    },
    enabled: Boolean(id),
  });

  const retryMutation = useMutation({
    mutationFn: async (checkId: string) => {
      const { data, error } = await api.POST(
        "/api/applications/{application_id}/checks/{check_id}/retry",
        { params: { path: { application_id: id!, check_id: checkId } } },
      );
      if (error) throw error;
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["application", id] });
    },
  });

  if (query.isLoading) return <p className="muted">Загрузка…</p>;
  if (query.isError || !query.data) {
    return <p role="alert" className="alert">Не удалось загрузить заявку.</p>;
  }

  const app = query.data;
  const canRetry = employee?.role === "risk_manager";
  const canOperateFssp = employee?.role === "operator" || employee?.role === "risk_manager";

  const findLatestFsspCheck = (subjectType: "applicant" | "guarantor", subjectId: string | null) => {
    const matches = app.checks.filter(
      (c) => c.source === "fssp" && c.subject_type === subjectType && (c.subject_id ?? null) === subjectId,
    );
    if (matches.length === 0) return null;
    return matches.reduce((latest, c) => (c.started_at > latest.started_at ? c : latest));
  };

  const latestDecision = app.decisions.length > 0 ? app.decisions[app.decisions.length - 1] : null;
  const earlierDecisions = app.decisions.length > 1 ? app.decisions.slice(0, -1) : [];
  const stageB = latestStageBPayload(app.external_snapshots);

  // «Нужно сделать» — только если что-то реально требуется. Стадия А «передано
  // оператору» — не действие для нас: ждём звонка и стадии Б.
  const actionItems: { key: string; text: string; href?: string }[] = [];
  if (latestDecision?.outcome === "manual_review" && latestDecision.stage === "B") {
    if (latestDecision.reason_code === "FSSP_NOT_CHECKED") {
      actionItems.push({ key: "fssp", text: "Проверить ФССП — без этого одобрение недоступно", href: "#fssp" });
    } else {
      actionItems.push({ key: "gray", text: "Серая зона — нужно решение риск-менеджера или оператора" });
    }
  }
  if (latestDecision?.freeze) {
    actionItems.push({
      key: "freeze",
      text: "Совпадение по перечню ждёт решения комплаенса",
      href: "/compliance",
    });
  }
  for (const subject of app.fssp_post_decision_alert_subjects) {
    actionItems.push({
      key: `fssp-late-${subject}`,
      text: `ФССП (${subjectTypeLabel(subject)}) найдено после того, как решение уже принято — пересмотрите вручную`,
      href: "#fssp",
    });
  }

  // Почему такое решение: сработавшие правила (или, если их нет, причина целиком
  // — reason_text, например повторная заморозка по перечню), затем сравнения с
  // порогами. Пустоты «стоп-факторов нет» там, где решение — отказ, быть не может.
  const reasons = latestDecision
    ? latestDecision.triggered_rules_text.length > 0
      ? latestDecision.triggered_rules_text
      : latestDecision.reason_text
        ? [latestDecision.reason_text]
        : []
    : [];
  const comparisons = latestDecision
    ? [bkiComparison(latestDecision), ptiComparison(latestDecision)].filter((line): line is string => line !== null)
    : [];
  const noReasonsText =
    latestDecision?.outcome === "approve"
      ? "Стоп-факторов нет."
      : latestDecision?.stage === "A" && latestDecision.outcome === "manual_review"
        ? "Стоп-факторов не сработало — заявка передана оператору для звонка и стадии Б."
        : "Сработавших стоп-факторов нет.";

  const verdictReason = reasons[0] ?? (latestDecision ? noReasonsText : null);

  const clientBlock = clientRows({
    clientName: app.client_name,
    phone: (app.profile?.phone as string | undefined) ?? null,
    ocrFields: app.ocr_result?.fields ?? null,
    stageB,
    now: new Date(),
  });
  const dealBlock = dealRows({
    goodsName: app.goods_name,
    goodsPrice: app.goods_price,
    goodsCategory: app.goods_category,
    downPayment: app.down_payment,
    termMonths: app.term_months,
    tariff: app.tariff,
    approvedAmount: latestDecision?.approved_amount ?? null,
    approvedTerm: latestDecision?.approved_term ?? null,
    approvedTariff: latestDecision?.offered_tariff ?? null,
  });

  return (
    <section>
      <p>
        <Link to="/applications">← к списку заявок</Link>
      </p>

      <div className={`verdict ${decisionVerdictClass(latestDecision?.outcome)}`}>
        <div>
          <p className="verdict-title">
            {latestDecision ? (
              latestDecision.outcome === "manual_review" ? (
                <Term
                  tip={
                    latestDecision.stage === "A"
                      ? "На стадии А одобрения не бывает: заявка уходит оператору, который звонит клиенту и запускает стадию Б."
                      : "Ни одобрить, ни отказать автоматически нельзя — нужно решение риск-менеджера или оператора вручную."
                  }
                >
                  {decisionOutcomeLabel(latestDecision.stage, latestDecision.outcome)}
                </Term>
              ) : (
                decisionOutcomeLabel(latestDecision.stage, latestDecision.outcome)
              )
            ) : (
              "В обработке"
            )}
          </p>
          {verdictReason && <p className="verdict-reason">{verdictReason}</p>}
          <p className="verdict-meta">
            <span>Заявка №{app.number} (у заказчика {app.external_id})</span>
            <span>{app.client_name ?? "Клиент скрыт"}</span>
            <span>{formatAmount(app.requested_amount)} ₽ на {app.term_months} мес.</span>
            <span>подана {formatDateTime(app.submitted_at)}</span>
            {latestDecision && (
              <span>
                <Term tip="Стадия А — сразу после подачи, без кредитной истории, только бесплатные проверки. Стадия Б — после звонка оператора, с запросом в БКИ.">
                  стадия {stageLabel(latestDecision.stage)}
                </Term>
              </span>
            )}
          </p>
        </div>
        {latestDecision?.score != null && (
          <div className="verdict-score">
            <div className="verdict-score-value">
              {wholeNumber(latestDecision.score)}{" "}
              <span className="unit">
                <Term tip="Скоринговый балл кредитного бюро (БКИ) — не наш собственный расчёт. Шкала бюро: чем выше, тем надёжнее заёмщик по данным о прошлых кредитах.">
                  баллов БКИ
                </Term>
              </span>
            </div>
            {latestDecision.score_auto_approve_above != null && latestDecision.score_auto_reject_below != null && (
              <>
                <div className="score-scale">
                  <div
                    className="marker"
                    style={{
                      left: `${Math.min(100, Math.max(0, (Number(latestDecision.score) / 999) * 100))}%`,
                    }}
                  />
                </div>
                <div className="score-scale-labels">
                  <span>0</span>
                  <span>{wholeNumber(latestDecision.score_auto_reject_below)} — отказ</span>
                  <span>{wholeNumber(latestDecision.score_auto_approve_above)} — одобрение</span>
                  <span>999</span>
                </div>
              </>
            )}
          </div>
        )}
      </div>

      {actionItems.length > 0 && (
        <div className="action-panel">
          <h2>Нужно сделать</h2>
          {actionItems.map((item) => (
            <div className="action-item" key={item.key}>
              <span>{item.text}</span>
              {item.href && (
                <a className="btn btn-small" href={item.href}>
                  Открыть
                </a>
              )}
            </div>
          ))}
        </div>
      )}

      {latestDecision && (
        <div className="card">
          <h2>Почему такое решение</h2>
          {reasons.length === 0 && <p className="muted" style={{ margin: 0 }}>{noReasonsText}</p>}
          {reasons.map((text, i) => (
            <div className="rule-hit" key={i}>
              <span className="dot" />
              <span>{text}</span>
            </div>
          ))}
          {comparisons.map((line) => (
            <div className="rule-hit" key={line}>
              <span className="dot" />
              <span>{line}</span>
            </div>
          ))}
          {latestDecision.approved_amount != null && (
            <p className="muted" style={{ marginTop: 10, marginBottom: 0 }}>
              Одобрено {formatAmount(latestDecision.approved_amount)} ₽ на {latestDecision.approved_term} мес.
              {latestDecision.offered_tariff && ` · тариф «${tariffLabel(latestDecision.offered_tariff)}»`}
              {latestDecision.required_down_pay != null &&
                ` · первый взнос от ${formatAmount(latestDecision.required_down_pay)} ₽`}
            </p>
          )}
        </div>
      )}

      {(clientBlock.length > 0 || dealBlock.length > 0) && (
        <div className="section-group">
          {clientBlock.length > 0 && (
            <div className="card">
              <h2>Клиент</h2>
              <dl className="kv-grid">
                {clientBlock.map((row) => (
                  <div key={row.label} style={{ display: "contents" }}>
                    <dt>{row.label}</dt>
                    <dd>{row.value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}
          {dealBlock.length > 0 && (
            <div className="card">
              <h2>Сделка</h2>
              <dl className="kv-grid">
                {dealBlock.map((row) => (
                  <div key={row.label} style={{ display: "contents" }}>
                    <dt>{row.label}</dt>
                    <dd>{row.value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}
        </div>
      )}

      {earlierDecisions.length > 0 && (
        <div className="card">
          <h2>Предыдущие решения</h2>
          {earlierDecisions.map((d) => (
            <p key={d.id} className="muted" style={{ margin: "4px 0" }}>
              Стадия {stageLabel(d.stage)}: {decisionOutcomeLabel(d.stage, d.outcome)}
              {d.freeze && " · заморожено"} — {formatDateTime(d.created_at)}
            </p>
          ))}
        </div>
      )}

      {app.manual_overrides.length > 0 && (
        <div className="card">
          <h2>Итог оператора</h2>
          {app.manual_overrides.map((o) => (
            <p key={o.id} style={{ margin: "4px 0" }}>
              {decisionOutcomeLabel(o.decision_stage, o.from_outcome)} →{" "}
              {decisionOutcomeLabel(o.decision_stage, o.to_outcome)}{" "}
              <span className="muted">
                ({reasonCodeLabel(o.reason_code)})
                {o.comment && `: ${o.comment}`}
              </span>
            </p>
          ))}
        </div>
      )}

      <h2>Что проверили</h2>
      <table className="table">
        <thead>
          <tr>
            <th>Источник</th>
            <th>Кто</th>
            <th>Результат</th>
            <th>Время</th>
            <th>Ошибка</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {/* T17, шаг 3 (ваше явное указание): МВД не показываем — файл
              недействительных паспортов отменён в 2023, источник по факту
              не подключён, строка только вводила бы в заблуждение. */}
          {app.checks.filter((c) => c.source !== "mvd").map((c) => (
            <tr key={c.id}>
              <td>
                <SourceBadge source={c.source} />
              </td>
              <td className="muted">{subjectTypeLabel(c.subject_type)}</td>
              <td>{checkResultLabel(c.source, c.status)}</td>
              <td className="muted">{formatDateTime(c.started_at)}</td>
              <td className="muted">{c.error_text ?? "—"}</td>
              <td>
                {c.can_retry && canRetry && (
                  <button
                    className="btn btn-small"
                    disabled={retryMutation.isPending}
                    onClick={() => retryMutation.mutate(c.id)}
                  >
                    Повторить
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {retryMutation.isError && <p role="alert" className="alert">Не удалось повторить проверку.</p>}

      <p className="muted">
        Федресурс (сведения о банкротстве) в автоматический конвейер не входит — проверьте вручную на{" "}
        <a href="https://fedresurs.ru" target="_blank" rel="noreferrer">
          fedresurs.ru
        </a>
        .
      </p>

      <h2 id="fssp">ФССП</h2>
      <FsspPanel
        applicationId={app.id}
        subjectType="applicant"
        subjectLabel="заявитель"
        subjectId={null}
        latestCheck={findLatestFsspCheck("applicant", null)}
        canOperate={canOperateFssp}
        hasPostDecisionAlert={app.fssp_post_decision_alert_subjects.includes("applicant")}
      />
      {app.guarantor_profile && (
        <div className="card">
          <p style={{ marginTop: 0 }}>
            <strong>Поручитель</strong>
          </p>
          <table className="table table--compact">
            <tbody>
              {Object.entries(app.guarantor_profile)
                .filter(([, value]) => value != null && value !== "")
                .map(([field, value]) => (
                  <tr key={field}>
                    <td>{humanizeFieldPath(field)}</td>
                    <td>{field.endsWith("_date") ? formatDate(String(value)) : String(value)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
      {app.has_guarantor && app.guarantor_id && (
        <FsspPanel
          applicationId={app.id}
          subjectType="guarantor"
          subjectLabel="поручитель"
          subjectId={app.guarantor_id}
          latestCheck={findLatestFsspCheck("guarantor", app.guarantor_id)}
          canOperate={canOperateFssp}
          hasPostDecisionAlert={app.fssp_post_decision_alert_subjects.includes("guarantor")}
        />
      )}

      <h2>Документы</h2>
      {app.documents.length === 0 && <p className="muted">Документов нет.</p>}
      <ul style={{ listStyle: "none", padding: 0 }}>
        {app.documents.map((doc) => (
          <li key={doc.id} className="card">
            {documentKindLabel(doc.kind)} <span className="muted">· {formatDateTime(doc.uploaded_at)}</span>{" "}
            <button className="btn btn-small" onClick={() => setOpenDocumentId(doc.id)}>
              показать
            </button>
            {openDocumentId === doc.id && (
              <div style={{ marginTop: 8 }}>
                <img
                  src={`/api/applications/${app.id}/documents/${doc.id}`}
                  alt={documentKindLabel(doc.kind)}
                  style={{ maxWidth: 320, display: "block", borderRadius: 6, border: "1px solid var(--color-border)" }}
                />
              </div>
            )}
          </li>
        ))}
      </ul>

      <h2>История</h2>
      <table className="table">
        <thead>
          <tr>
            <th>Из</th>
            <th>В</th>
            <th>Кто</th>
            <th>Когда</th>
          </tr>
        </thead>
        <tbody>
          {app.status_history.map((h, i) => (
            <tr key={i}>
              <td>
                <Badge value={h.from_status} label={h.from_status ? applicationStatusLabel(h.from_status) : undefined} />
              </td>
              <td>
                <Badge value={h.to_status} label={applicationStatusLabel(h.to_status)} />
              </td>
              <td className="muted">{actorTypeLabel(h.actor_type)}</td>
              <td className="muted">{formatDateTime(h.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* Сырьё — не в основном виде (T17, часть 2): распознанные поля паспорта
          с уверенностью и полные снимки анкеты, для того, кто проверяет
          распознавание или ищет, что именно менял оператор. */}
      <details className="card" style={{ marginTop: 24 }}>
        <summary style={{ cursor: "pointer", fontWeight: 600 }}>Подробности</summary>
        {app.ocr_result ? (
          <>
            <h3 style={{ fontSize: 13, color: "var(--color-text-muted)", margin: "12px 0 6px" }}>
              Распознавание паспорта · мин. уверенность {app.ocr_result.min_confidence ?? "—"} ·{" "}
              {formatDateTime(app.ocr_result.created_at)}
            </h3>
            <table className="table table--compact">
              <thead>
                <tr>
                  <th>Поле</th>
                  <th>Значение</th>
                  <th>Уверенность</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries((app.ocr_result.fields.fields as Record<string, unknown>) ?? {}).map(
                  ([field, value]) => {
                    const v = value as { value?: unknown; confidence?: number };
                    return (
                      <tr key={field}>
                        <td>{humanizeFieldPath(field)}</td>
                        <td>{String(v.value ?? "—")}</td>
                        <td className="muted">{v.confidence ?? "—"}</td>
                      </tr>
                    );
                  },
                )}
              </tbody>
            </table>
          </>
        ) : null}
        <h3 style={{ fontSize: 13, color: "var(--color-text-muted)", margin: "12px 0 6px" }}>Снимки анкеты</h3>
        <SnapshotHistory snapshots={app.external_snapshots} />
      </details>
    </section>
  );
}
