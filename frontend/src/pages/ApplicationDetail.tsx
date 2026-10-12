import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { Badge } from "../components/Badge";
import { FsspPanel } from "../components/FsspPanel";
import { SnapshotHistory } from "../components/SnapshotHistory";
import { SourceBadge } from "../components/SourceBadge";
import { VerdictTitle } from "../components/VerdictTitle";
import { ErrorState, LoadingState } from "../components/ui";
import { clientRows, dealRows, latestStageBPayload } from "../lib/card";
import { explainDecision } from "../lib/explain";
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

  if (query.isLoading) return <LoadingState />;
  if (query.isError || !query.data) {
    return <ErrorState message="Не удалось загрузить заявку." onRetry={() => void query.refetch()} />;
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
  // оператору» — не действие для нас: ждём звонка и стадии Б. T20: одна строка
  // с кнопкой сразу под вердиктом, без отдельной рамки.
  const actionItems: { key: string; text: string; href?: string; button?: string }[] = [];
  if (latestDecision?.outcome === "manual_review" && latestDecision.stage === "B") {
    if (latestDecision.reason_code === "FSSP_NOT_CHECKED") {
      actionItems.push({ key: "fssp", text: "Проверить ФССП — без этого одобрение недоступно", href: "#fssp", button: "К проверке ФССП" });
    } else {
      actionItems.push({ key: "gray", text: "решение риск-менеджера или оператора" });
    }
  }
  if (latestDecision?.freeze) {
    actionItems.push({
      key: "freeze",
      text: "Совпадение по перечню ждёт решения комплаенса",
      href: "/compliance",
      button: "Открыть комплаенс",
    });
  }
  for (const subject of app.fssp_post_decision_alert_subjects) {
    actionItems.push({
      key: `fssp-late-${subject}`,
      text: `ФССП (${subjectTypeLabel(subject)}) найдено после решения — пересмотрите вручную`,
      href: "#fssp",
      button: "К проверке ФССП",
    });
  }

  // T20: причина — один раз, в вердикте; «Почему» — только детали: что ещё
  // привело к решению («Причина») и что пройдено («В норме»). Пройденные
  // сравнения больше не попадают в список сработавших стоп-факторов.
  const explanation = latestDecision ? explainDecision(latestDecision) : null;

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
    approvedDownPay: latestDecision?.required_down_pay ?? null,
    installmentPayment: latestDecision?.installment_payment ?? null,
    installmentSource: latestDecision?.installment_payment_source ?? null,
  });

  return (
    <section>
      <p className="back-link">
        <Link to="/applications">← к списку заявок</Link>
      </p>

      <div className={`verdict ${decisionVerdictClass(latestDecision?.outcome)}`}>
        <div className="verdict-main">
          <VerdictTitle
            title={latestDecision ? decisionOutcomeLabel(latestDecision.stage, latestDecision.outcome) : "В обработке"}
            help={explanation?.help ?? null}
          />
          {explanation?.reason && <p className="verdict-reason">{explanation.reason}</p>}
          <p className="verdict-meta">
            <span>Заявка №{app.number} (у заказчика {app.external_id})</span>
            <span>{app.client_name ?? "Клиент скрыт"}</span>
            {app.term_months > 0 && <span>{formatAmount(app.requested_amount)} ₽ на {app.term_months} мес.</span>}
            <span>подана {formatDateTime(app.submitted_at)}</span>
            {latestDecision && <span>стадия {stageLabel(latestDecision.stage)}</span>}
          </p>
        </div>
        {latestDecision?.score != null && (
          <div className="verdict-score">
            <div className="verdict-score-value">
              {wholeNumber(latestDecision.score)} <span className="unit">баллов БКИ</span>
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

      {actionItems.map((item) => (
        <div className="next-step" key={item.key}>
          <span className="next-step-label">Нужно сделать:</span>
          <span>{item.text}</span>
          {item.href && (
            <a className="btn btn-primary btn-small" href={item.href}>
              {item.button ?? "Открыть"}
            </a>
          )}
        </div>
      ))}

      {explanation &&
        (explanation.causes.length > 0 || explanation.passed.length > 0 || explanation.unknown.length > 0) && (
          <section className="section">
            <h2>Почему такое решение</h2>
            {explanation.causes.length > 0 && (
              <>
                <h3 className="subhead">Причина</h3>
                <ul className="reason-list" data-testid="decision-causes">
                  {explanation.causes.map((text) => (
                    <li key={text} className="reason-item reason-item--cause">{text}</li>
                  ))}
                </ul>
              </>
            )}
            {explanation.passed.length > 0 && (
              <>
                <h3 className="subhead">В норме</h3>
                <ul className="reason-list reason-list--minor" data-testid="decision-passed">
                  {explanation.passed.map((text) => (
                    <li key={text} className="reason-item reason-item--pass">{text}</li>
                  ))}
                </ul>
              </>
            )}
            {explanation.unknown.length > 0 && (
              <ul className="reason-list reason-list--minor" data-testid="decision-unknown">
                {explanation.unknown.map((text) => (
                  <li key={text} className="reason-item">{text}</li>
                ))}
              </ul>
            )}
          </section>
        )}

      {(clientBlock.length > 0 || dealBlock.length > 0) && (
        <div className="section-columns">
          {clientBlock.length > 0 && (
            <section className="section">
              <h2>Клиент</h2>
              <KeyValue rows={clientBlock} />
            </section>
          )}
          {dealBlock.length > 0 && (
            <section className="section">
              <h2>Сделка</h2>
              <KeyValue rows={dealBlock} />
            </section>
          )}
        </div>
      )}

      {earlierDecisions.length > 0 && (
        <section className="section">
          <h2>Предыдущие решения</h2>
          <ul className="plain-list">
            {earlierDecisions.map((d) => (
              <li key={d.id} className="muted">
                Стадия {stageLabel(d.stage)}: {decisionOutcomeLabel(d.stage, d.outcome)}
                {d.freeze && " · заморожено"} — {formatDateTime(d.created_at)}
              </li>
            ))}
          </ul>
        </section>
      )}

      {app.manual_overrides.length > 0 && (
        <section className="section">
          <h2>Итог оператора</h2>
          <ul className="plain-list">
            {app.manual_overrides.map((o) => (
              <li key={o.id}>
                {decisionOutcomeLabel(o.decision_stage, o.from_outcome)} →{" "}
                {decisionOutcomeLabel(o.decision_stage, o.to_outcome)}{" "}
                <span className="muted">
                  ({reasonCodeLabel(o.reason_code)})
                  {o.comment && `: ${o.comment}`}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="section">
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
        <p className="hint">
          Федресурс (сведения о банкротстве) в автоматический конвейер не входит — проверьте вручную на{" "}
          <a href="https://fedresurs.ru" target="_blank" rel="noreferrer">
            fedresurs.ru
          </a>
          .
        </p>
      </section>

      <section className="section">
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
          <div className="subsection">
            <h3 className="subhead">Поручитель</h3>
            <KeyValue
              rows={Object.entries(app.guarantor_profile)
                .filter(([, value]) => value != null && value !== "")
                .map(([field, value]) => ({
                  label: humanizeFieldPath(field),
                  value: field.endsWith("_date") ? formatDate(String(value)) : String(value),
                }))}
            />
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
      </section>

      <section className="section">
        <h2>Документы</h2>
        {app.documents.length === 0 && <p className="muted">Документов нет.</p>}
        <ul className="plain-list">
          {app.documents.map((doc) => (
            <li key={doc.id}>
              <div className="row">
                <span>{documentKindLabel(doc.kind)}</span>
                <span className="muted">{formatDateTime(doc.uploaded_at)}</span>
                <button className="btn btn-small" onClick={() => setOpenDocumentId(doc.id)}>
                  Показать
                </button>
              </div>
              {openDocumentId === doc.id && (
                <img
                  className="document-preview"
                  src={`/api/applications/${app.id}/documents/${doc.id}`}
                  alt={documentKindLabel(doc.kind)}
                />
              )}
            </li>
          ))}
        </ul>
      </section>

      <section className="section">
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
      </section>

      {/* Сырьё — не в основном виде (T17, часть 2): распознанные поля паспорта
          с уверенностью и полные снимки анкеты, для того, кто проверяет
          распознавание или ищет, что именно менял оператор. */}
      <details className="disclosure">
        <summary>Подробности</summary>
        {app.ocr_result ? (
          <>
            <h3 className="subhead">
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
        <h3 className="subhead">Снимки анкеты</h3>
        <SnapshotHistory snapshots={app.external_snapshots} />
      </details>
    </section>
  );
}

function KeyValue({ rows }: { rows: { label: string; value: string }[] }) {
  return (
    <dl className="kv-grid">
      {rows.map((row) => (
        <div key={row.label} className="kv-row">
          <dt>{row.label}</dt>
          <dd>{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}
