import { useState } from "react";
import type React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { useAuth } from "../auth/AuthProvider";
import { Badge } from "../components/Badge";
import { ClientRatingPanel } from "../components/ClientRating";
import { AiAnalysisPanel } from "../components/AiAnalysisPanel";
import { FedresursPanel } from "../components/FedresursPanel";
import { FsspPanel } from "../components/FsspPanel";
import { SnapshotHistory } from "../components/SnapshotHistory";
import { SourceBadge } from "../components/SourceBadge";
import { VerdictTitle } from "../components/VerdictTitle";
import { ErrorState, LoadingState } from "../components/ui";
import { ageYears, clientRows, dealRows, latestStageBPayload, ocrValue } from "../lib/card";
import { explainDecision } from "../lib/explain";
import { formatAmount, formatDate, formatDateTime } from "../lib/format";
import {
  actorTypeLabel,
  applicationStatusLabel,
  checkResultLabel,
  decisionOutcomeLabel,
  documentKindLabel,
  humanizeFieldPath,
  overrideOutcomeLabel,
  passportCheckLabel,
  passportCheckStatusLabel,
  reasonCodeLabel,
  sourceMeta,
  stageLabel,
  subjectTypeLabel,
} from "../lib/labels";

type Decision = components["schemas"]["DecisionOut"];
type CheckRow = components["schemas"]["CheckOut"];
type ManualOverride = components["schemas"]["ManualOverrideOut"];
type EventTone = "ok" | "bad" | "warn" | "info" | "wait";

// Числа в карточке — без копеек (780, а не 780.00): баллы и пороги целые.
function wholeNumber(value: number | string): string {
  return String(Math.round(Number(value)));
}

function verdictTone(outcome: string | undefined): string {
  if (outcome === "approve") return "pf-verdict--ok";
  if (outcome === "reject") return "pf-verdict--bad";
  if (outcome === "manual_review") return "pf-verdict--warn";
  return "pf-verdict--wait";
}

function decisionEventTone(d: Decision): EventTone {
  if (d.outcome === "approve") return "ok";
  if (d.outcome === "reject") return "bad";
  return d.stage === "B" ? "warn" : "info";
}

const EVENT_ICON: Record<EventTone, string> = { ok: "✓", bad: "✕", warn: "!", info: "→", wait: "…" };

// Инициалы для аватара: первые буквы фамилии и имени.
function initials(name: string | null): string {
  if (!name) return "?";
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]!.toUpperCase())
    .join("");
}

// T23: оператор в CRM сменил статус у себя после решения системы — что
// решила система, что поставил оператор и почему, обычными словами.
function OperatorOverride({ override: o }: { override: ManualOverride }) {
  const tone = o.reason_code === "PARTNER_OPERATOR_OVERRIDE_APPROVE" ? "approve" : "reject";
  return (
    <div className={`override override--${tone}`}>
      <div className="override__head">
        <span className="override__title">{reasonCodeLabel(o.reason_code)}</span>
      </div>
      <dl className="override__rows">
        <dt>Решение системы</dt>
        <dd>{overrideOutcomeLabel(o.decision_stage, o.from_outcome)}</dd>
        <dt>Итог оператора</dt>
        <dd>
          <b>{overrideOutcomeLabel(o.decision_stage, o.to_outcome)}</b>
        </dd>
        <dt>Причина</dt>
        <dd>{o.comment ?? "оператор не указал"}</dd>
      </dl>
    </div>
  );
}

// Проверки стадии одной строкой точек; полный список с паспортом по пунктам
// и кнопкой «Повторить» — по кнопке «подробнее».
function CheckSummary({
  checks,
  passport,
  canRetry,
  retry,
  busy,
}: {
  checks: CheckRow[];
  passport: PassportCheck[];
  canRetry: boolean;
  retry: (checkId: string) => void;
  busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  if (checks.length === 0 && passport.length === 0) return null;
  const passportTone = passport.some((i) => i.status === "fail")
    ? "bad"
    : passport.some((i) => i.status !== "ok")
      ? "warn"
      : "ok";
  const passportOk = passport.filter((i) => i.status === "ok").length;
  return (
    <div className="ev__checks">
      <ul className="ev__chips">
        {passport.length > 0 && (
          <li className={`ev__chip ev__chip--${passportTone}`}>
            Паспорт {passportOk}/{passport.length}
          </li>
        )}
        {checks.map((c) => (
          <li key={c.id} className={`ev__chip ev__chip--${checkTone(c.status)}`}>
            {sourceMeta(c.source).label}
            {c.subject_type === "guarantor" && " (поручитель)"}
            {checkTone(c.status) !== "ok" && <span className="ev__chip-note">· {checkResultLabel(c.source, c.status)}</span>}
          </li>
        ))}
        <li>
          <button type="button" className="btn-link ev__more" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
            {open ? "скрыть" : "подробнее"}
          </button>
        </li>
      </ul>
      {open && (
        <CheckList
          checks={checks}
          canRetry={canRetry}
          retry={retry}
          busy={busy}
          leading={passport.length > 0 ? <PassportRow items={passport} /> : null}
        />
      )}
    </div>
  );
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
  // T23: ролей три — повтор проверки у admin, ФССП у admin и operator.
  const canRetry = employee?.role === "admin";
  const canOperateFssp = employee?.role === "operator" || employee?.role === "admin";
  const retry = (checkId: string) => retryMutation.mutate(checkId);

  const findLatestCheck = (
    source: "fssp" | "fedresurs" | "fns_inn", subjectType: "applicant" | "guarantor", subjectId: string | null,
  ) => {
    const matches = app.checks.filter(
      (c) => c.source === source && c.subject_type === subjectType && (c.subject_id ?? null) === subjectId,
    );
    if (matches.length === 0) return null;
    return matches.reduce((latest, c) => (c.started_at > latest.started_at ? c : latest));
  };

  const latestDecision = app.decisions.length > 0 ? app.decisions[app.decisions.length - 1]! : null;
  const stageB = latestStageBPayload(app.external_snapshots);
  const explanation = latestDecision ? explainDecision(latestDecision) : null;

  const decisionsA = app.decisions.filter((d) => d.stage === "A");
  const decisionsB = app.decisions.filter((d) => d.stage === "B");
  const latestA = decisionsA.length > 0 ? decisionsA[decisionsA.length - 1]! : null;
  const latestB = decisionsB.length > 0 ? decisionsB[decisionsB.length - 1]! : null;
  const rejectedAtA = latestA?.outcome === "reject" && !latestB;
  const reviewSnapshots = app.external_snapshots.filter((s) => s.stage === "B");
  const reviewedAt = reviewSnapshots.length > 0 ? reviewSnapshots[reviewSnapshots.length - 1]!.received_at : null;

  const visibleChecks = app.checks.filter((c) => c.source !== "mvd");
  // Ручные проверки (ФССП, Федресурс) и поручитель — после звонка, к стадии Б.
  const isManual = (c: CheckRow) => c.source === "fssp" || c.source === "fedresurs";
  const checksA = visibleChecks.filter((c) => c.subject_type !== "guarantor" && !isManual(c));
  const checksB = visibleChecks.filter((c) => c.subject_type === "guarantor" || isManual(c));

  // T23 (по отзыву): отдельного списка дел нет. Что требует действия здесь —
  // в закреплённом блоке «Нужно ваше действие» с самими проверками; что
  // делается не здесь (серая зона в CRM, проверка по перечням) — строкой под
  // решением.
  const applicantFssp = findLatestCheck("fssp", "applicant", null);
  const applicantFedresurs = findLatestCheck("fedresurs", "applicant", null);
  const grayStageB = latestDecision?.outcome === "manual_review" && latestDecision.stage === "B";
  const fsspNeedsAction =
    (grayStageB && latestDecision?.reason_code === "FSSP_NOT_CHECKED") ||
    app.fssp_post_decision_alert_subjects.length > 0;
  const grayInCrm = grayStageB && latestDecision?.reason_code !== "FSSP_NOT_CHECKED";
  // Банкротство проверяется вручную — пока по заявителю нет отметки и заявка
  // ещё в работе (после отказа или истечения — незачем).
  const fedresursNeedsAction =
    !applicantFedresurs && app.decisions.length > 0 && !["rejected_a", "rejected_b", "expired"].includes(app.status);
  const actionCount = Number(fsspNeedsAction) + Number(fedresursNeedsAction);

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

  // Поиск на Федресурсе: ИНН (если ФНС его нашла — точное совпадение) или ФИО;
  // дата рождения — чтобы сверить найденного однофамильца.
  const innOf = (subjectType: "applicant" | "guarantor", subjectId: string | null) => {
    const inn = findLatestCheck("fns_inn", subjectType, subjectId)?.parsed?.inn;
    return typeof inn === "string" && inn ? inn : null;
  };
  const birthText = (birth: string | null | undefined) => (birth ? formatDate(birth) : null);
  const guarantorName = app.guarantor_profile
    ? [app.guarantor_profile.last_name, app.guarantor_profile.first_name, app.guarantor_profile.middle_name]
        .filter((part) => typeof part === "string" && part)
        .join(" ")
    : null;

  // Профиль клиента: возраст из паспорта, отметки, анкета без ФИО (оно в заголовке).
  const birthDate = ocrValue(app.ocr_result?.fields ?? null, "birth_date");
  const age = birthDate ? ageYears(birthDate, new Date()) : null;
  const guarantorNeeded = (app.client_rating?.guarantor_reasons.length ?? 0) > 0;
  const profileRows = clientBlock.filter((row) => row.label !== "ФИО");
  const innFound = Boolean(innOf("applicant", null));

  // Лента событий заявки — от новых к старым. У последнего решения каждой
  // стадии — причины и проверки этой стадии.
  type FeedEvent = { key: string; at: string | null; tone: EventTone; title: string; body?: React.ReactNode };
  const events: FeedEvent[] = [
    {
      key: "submitted",
      at: app.submitted_at,
      tone: "ok" as const,
      title: app.ocr_result ? "Заявка подана, паспорт распознан" : "Заявка подана",
      body:
        app.documents.length > 0 ? (
          <p className="ev__text">
            Фото: {app.documents.map((d) => documentKindLabel(d.kind).toLowerCase()).join(", ")}.
          </p>
        ) : undefined,
    },
    ...app.decisions.map((d): FeedEvent => {
      const isLatestOfStage = d === (d.stage === "A" ? latestA : latestB);
      const outcome = decisionOutcomeLabel(d.stage, d.outcome);
      return {
        key: `decision-${d.id}`,
        at: d.created_at,
        tone: decisionEventTone(d),
        title: `Стадия ${stageLabel(d.stage)}: ${outcome[0]!.toLowerCase()}${outcome.slice(1)}`,
        body: isLatestOfStage ? (
          <>
            {/* Причина последнего решения — уже под заголовком решения, здесь только детали. */}
            {d === latestDecision && explanation ? (
              <DecisionReasons explanation={explanation} />
            ) : (
              d.reason_text && <p className="ev__text">{d.reason_text}</p>
            )}
            <CheckSummary
              checks={d.stage === "A" ? checksA : checksB}
              passport={d.stage === "A" ? app.passport_checks : []}
              canRetry={canRetry}
              retry={retry}
              busy={retryMutation.isPending}
            />
          </>
        ) : (
          d.reason_text && <p className="ev__text">{d.reason_text}</p>
        ),
      };
    }),
    ...(reviewedAt
      ? [{ key: "call", at: reviewedAt, tone: "ok" as const, title: "Оператор позвонил и нажал «Рассмотреть» в CRM" }]
      : []),
    ...app.manual_overrides.map(
      (o): FeedEvent => ({
        key: `override-${o.id}`,
        at: o.created_at,
        tone: o.reason_code === "PARTNER_OPERATOR_OVERRIDE_APPROVE" ? "ok" : "bad",
        title: "Решение оператора в CRM",
        body: <OperatorOverride override={o} />,
      }),
    ),
  ]
    // От новых к старым; при одинаковом времени — в порядке хода заявки
    // (подача → стадия А → звонок → стадия Б), только перевёрнутом.
    .map((e, order) => ({ e, order }))
    .sort((a, b) => (b.e.at ?? "").localeCompare(a.e.at ?? "") || b.order - a.order)
    .map(({ e }) => e);
  // Что ждём дальше — сверху ленты, без времени.
  if (!latestA && !reviewedAt && !latestB) {
    events.unshift({ key: "next", at: null, tone: "wait", title: "Идут проверки стадии А" });
  } else if (latestA && !rejectedAtA && !reviewedAt && !latestB) {
    events.unshift({
      key: "next",
      at: null,
      tone: "wait",
      title: "Ждём звонка оператора",
      body: <p className="ev__text">Стадия Б начнётся, когда оператор позвонит клиенту и нажмёт «Рассмотреть» в CRM.</p>,
    });
  }

  const ptiPercent = latestB?.pti != null ? Math.round(latestB.pti * 100) : null;
  const ptiMaxPercent = latestB?.pti_max != null ? Math.round(latestB.pti_max * 100) : null;
  const score = latestDecision?.score ?? null;

  const fsspSection = (
    <section className="mc" key="fssp">
      <div className="mc__head">
        <h3 id="fssp" className="mc__title">
          ФССП
        </h3>
        {fsspNeedsAction && <span className="action-tag">требуется действие</span>}
        {fsspNeedsAction && grayStageB && <span className="mc__why">без неё одобрение недоступно</span>}
      </div>
      <FsspPanel
        applicationId={app.id}
        subjectType="applicant"
        subjectLabel="заявитель"
        subjectId={null}
        latestCheck={applicantFssp}
        canOperate={canOperateFssp}
        hasPostDecisionAlert={app.fssp_post_decision_alert_subjects.includes("applicant")}
      />
      {app.has_guarantor && app.guarantor_id && (
        <FsspPanel
          applicationId={app.id}
          subjectType="guarantor"
          subjectLabel="поручитель"
          subjectId={app.guarantor_id}
          latestCheck={findLatestCheck("fssp", "guarantor", app.guarantor_id)}
          canOperate={canOperateFssp}
          hasPostDecisionAlert={app.fssp_post_decision_alert_subjects.includes("guarantor")}
        />
      )}
    </section>
  );

  const fedresursSection = (
    <section className="mc" key="fedresurs">
      <div className="mc__head">
        <h3 id="fedresurs" className="mc__title">
          Банкротство (Федресурс)
        </h3>
        {fedresursNeedsAction && <span className="action-tag">требуется действие</span>}
      </div>
      <FedresursPanel
        applicationId={app.id}
        subjectType="applicant"
        subjectLabel="заявитель"
        subjectId={null}
        fullName={app.client_name ?? null}
        birthDate={birthText(birthDate)}
        inn={innOf("applicant", null)}
        latestCheck={applicantFedresurs}
        canOperate={canOperateFssp}
      />
      {app.has_guarantor && app.guarantor_id && (
        <FedresursPanel
          applicationId={app.id}
          subjectType="guarantor"
          subjectLabel="поручитель"
          subjectId={app.guarantor_id}
          fullName={guarantorName || null}
          birthDate={birthText(app.guarantor_profile?.birth_date as string | undefined)}
          inn={innOf("guarantor", app.guarantor_id)}
          latestCheck={findLatestCheck("fedresurs", "guarantor", app.guarantor_id)}
          canOperate={canOperateFssp}
        />
      )}
    </section>
  );

  // T23 (выбор владельца, вариант 5 «Профиль и лента»): слева — профиль
  // клиента (кто это, анкета, рейтинг, анализ ИИ), справа — заявка: решение,
  // закреплённый блок «Нужно ваше действие», главные цифры и лента событий.
  return (
    <div className={latestDecision?.outcome === "manual_review" ? "pf pf--gray" : "pf"}>
      <p className="back-link pf-back">
        <Link to="/applications">← Заявки</Link>
      </p>
      <div className="pf-cover" aria-hidden="true" />

      <div className="pf-layout">
        <aside className="pf-profile">
          <section className="pf-card pf-person">
            <div className="pf-avatar" aria-hidden="true">
              {initials(app.client_name)}
            </div>
            <h2 className="pf-name">{app.client_name ?? "Клиент скрыт"}</h2>
            <p className="pf-sub">
              {[age != null ? `${age} лет` : null, app.goods_category].filter(Boolean).join(" · ") || "данные паспорта не распознаны"}
            </p>
            <div className="pf-tags">
              {innFound && <span className="pill pill-ok">ИНН найден</span>}
              {app.has_guarantor ? (
                <span className="pill pill-mute">есть поручитель</span>
              ) : guarantorNeeded ? (
                <span className="pill pill-bad">нужен поручитель</span>
              ) : null}
            </div>
            {profileRows.length > 0 && <KeyValue rows={profileRows} />}
            {app.guarantor_profile && (
              <details className="fold">
                <summary>Данные поручителя</summary>
                <KeyValue
                  rows={Object.entries(app.guarantor_profile)
                    .filter(([, value]) => value != null && value !== "")
                    .map(([field, value]) => ({
                      label: humanizeFieldPath(field),
                      value: field.endsWith("_date") ? formatDate(String(value)) : String(value),
                    }))}
                />
              </details>
            )}

            {app.client_rating && <ClientRatingPanel rating={app.client_rating} />}

            <section className="pf-ai">
              <div className="pf-ai__head">
                <h2 className="pf-section-title">Анализ ИИ</h2>
                <span className="soon-tag">в разработке</span>
              </div>
              <AiAnalysisPanel />
            </section>
          </section>
        </aside>

        <div className="pf-main">
          <header className={`pf-verdict ${verdictTone(latestDecision?.outcome)}`}>
            <div className="pf-verdict__main">
              <div className="pf-kicker">
                Заявка №{app.number} · подана {formatDateTime(app.submitted_at)}
              </div>
              <div className="pf-verdict__title">
                <VerdictTitle
                  title={latestDecision ? decisionOutcomeLabel(latestDecision.stage, latestDecision.outcome) : "В обработке"}
                  help={explanation?.help ?? null}
                />
                <Badge value={app.status} label={applicationStatusLabel(app.status)} />
              </div>
              {explanation?.reason && <p className="pf-verdict__reason">{explanation.reason}</p>}
              {grayInCrm && <p className="verdict-action">Решение принимает оператор в CRM.</p>}
              {latestDecision?.freeze && (
                <p className="verdict-action">
                  Похож на человека из перечня Росфинмониторинга — проверьте, он ли это.
                  <Link className="btn btn-primary btn-small" to="/compliance">
                    К проверке по перечням
                  </Link>
                </p>
              )}
            </div>
            <dl className="pf-verdict__deal">
              <div>
                <dt>Товар</dt>
                <dd>{app.goods_name && app.goods_name !== "—" ? app.goods_name : "—"}</dd>
              </div>
              <div>
                <dt>Сумма</dt>
                <dd>{app.term_months > 0 ? `${formatAmount(app.requested_amount)} ₽ · ${app.term_months} мес.` : "после «Рассмотреть»"}</dd>
              </div>
              <div>
                <dt>В CRM</dt>
                <dd>{app.external_id}</dd>
              </div>
            </dl>
          </header>

          {actionCount > 0 && (
            <div className="pf-card pf-action">
              <div className="pf-action__head">
                <span className="pf-action__icon" aria-hidden="true">
                  !
                </span>
                Нужно ваше действие — {actionCount === 1 ? "1 проверка" : `${actionCount} проверки`}
              </div>
              {fsspNeedsAction && fsspSection}
              {fedresursNeedsAction && fedresursSection}
            </div>
          )}

          <div className="pf-kpis">
            <div className="pf-card pf-kpi">
              <div className="pf-kpi__label">Балл БКИ</div>
              <div className="pf-kpi__value">{score != null ? wholeNumber(score) : "—"}</div>
              {score != null &&
              latestDecision?.score_auto_approve_above != null &&
              latestDecision.score_auto_reject_below != null ? (
                <>
                  <div
                    className="score-scale"
                    style={
                      {
                        "--reject-at": `${(Number(latestDecision.score_auto_reject_below) / 999) * 100}%`,
                        "--approve-at": `${(Number(latestDecision.score_auto_approve_above) / 999) * 100}%`,
                      } as React.CSSProperties
                    }
                  >
                    <div className="marker" style={{ left: `${Math.min(100, Math.max(0, (Number(score) / 999) * 100))}%` }} />
                  </div>
                  <div className="pf-kpi__hint">
                    отказ ниже {wholeNumber(latestDecision.score_auto_reject_below)} · одобрение от{" "}
                    {wholeNumber(latestDecision.score_auto_approve_above)}
                  </div>
                </>
              ) : (
                <div className="pf-kpi__hint">{score != null ? "пороги не заданы" : "после стадии Б"}</div>
              )}
            </div>
            <div className="pf-card pf-kpi">
              <div className="pf-kpi__label">Долговая нагрузка (ПДН)</div>
              <div className="pf-kpi__value">
                {ptiPercent != null ? `${ptiPercent}%` : "—"}
                {ptiPercent != null && ptiMaxPercent != null && <span className="pf-kpi__of"> из {ptiMaxPercent}%</span>}
              </div>
              {ptiPercent != null && ptiMaxPercent != null ? (
                <div className="pti-bar">
                  <i
                    className={ptiPercent > ptiMaxPercent ? "pti-bar__fill pti-bar__fill--bad" : "pti-bar__fill"}
                    style={{ width: `${Math.min(100, ptiPercent)}%` }}
                  />
                  <u className="pti-bar__limit" style={{ left: `${Math.min(100, ptiMaxPercent)}%` }} />
                </div>
              ) : (
                <div className="pf-kpi__hint">после стадии Б</div>
              )}
            </div>
            <div className="pf-card pf-kpi">
              <div className="pf-kpi__label">Платёж в месяц</div>
              <div className="pf-kpi__value">
                {latestDecision?.installment_payment ? `${formatAmount(latestDecision.installment_payment)} ₽` : "—"}
              </div>
              <div className="pf-kpi__hint">
                {app.term_months > 0
                  ? `взнос ${formatAmount(app.down_payment)} ₽ · ${app.term_months} мес.`
                  : "после «Рассмотреть»"}
              </div>
            </div>
          </div>

          <section className="pf-feed">
            <h2 className="pf-section-title">Ход заявки</h2>
            <ol className="feed">
              {events.map((e) => (
                <li key={e.key} className={`ev ev--${e.tone}`}>
                  <span className="ev__icon" aria-hidden="true">
                    {EVENT_ICON[e.tone]}
                  </span>
                  <div className="pf-card ev__card">
                    <div className="ev__head">
                      <h3 className="ev__title">{e.title}</h3>
                      {e.at && <span className="ev__time">{formatDateTime(e.at)}</span>}
                    </div>
                    {e.body}
                  </div>
                </li>
              ))}
            </ol>
            {retryMutation.isError && <p role="alert" className="alert">Не удалось повторить проверку.</p>}
          </section>

          {(!fsspNeedsAction || !fedresursNeedsAction) && (
            <div className="pf-card pf-manual">
              <h2 className="pf-section-title">Ручные проверки</h2>
              {!fsspNeedsAction && fsspSection}
              {!fedresursNeedsAction && fedresursSection}
            </div>
          )}

          {/* Анкета сделки, проверки таблицей, документы и история — одна панель; длинное — по кнопке. */}
          <div className="pf-card pf-data">
            {dealBlock.length > 0 && (
              <section className="pf-data__row">
                <h2 className="pf-section-title">Условия сделки</h2>
                <KeyValue rows={dealBlock} />
              </section>
            )}

            <section className="pf-data__row">
              <h2 className="pf-section-title">Что проверили</h2>
              <details className="fold">
                <summary>Все проверки одной таблицей ({visibleChecks.length})</summary>
                <table className="table">
                  <thead>
                    <tr>
                      <th>Источник</th>
                      <th>Кто</th>
                      <th>Результат</th>
                      <th>Время</th>
                      <th>Ошибка</th>
                    </tr>
                  </thead>
                  <tbody>
                    {/* T17, шаг 3 (ваше явное указание): МВД не показываем — файл
                        недействительных паспортов отменён в 2023. */}
                    {visibleChecks.map((c) => (
                      <tr key={c.id}>
                        <td>
                          <SourceBadge source={c.source} />
                        </td>
                        <td className="muted">{subjectTypeLabel(c.subject_type)}</td>
                        <td>{checkResultLabel(c.source, c.status)}</td>
                        <td className="muted">{formatDateTime(c.started_at)}</td>
                        <td className="muted">{c.error_text ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            </section>

            <section className="pf-data__row">
              <h2 className="pf-section-title">Документы</h2>
              {app.documents.length === 0 && <p className="muted">Документов нет.</p>}
              <ul className="plain-list">
                {app.documents.map((doc) => (
                  <li key={doc.id}>
                    <div className="row">
                      <span>{documentKindLabel(doc.kind)}</span>
                      <span className="muted">{formatDateTime(doc.uploaded_at)}</span>
                      <button className="btn btn-small" onClick={() => setOpenDocumentId(openDocumentId === doc.id ? null : doc.id)}>
                        {openDocumentId === doc.id ? "Скрыть" : "Показать"}
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

            <section className="pf-data__row">
              <h2 className="pf-section-title">История</h2>
              <details className="fold">
                <summary>Показать историю статусов ({app.status_history.length})</summary>
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
              </details>
            </section>

            {/* Сырьё — не в основном виде (T17, часть 2): распознанные поля паспорта
                с уверенностью и полные снимки анкеты, для того, кто проверяет
                распознавание или ищет, что именно менял оператор. */}
            <details className="disclosure pf-data__row">
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
          </div>
        </div>
      </div>
    </div>
  );
}

function DecisionReasons({ explanation }: { explanation: NonNullable<ReturnType<typeof explainDecision>> }) {
  if (explanation.causes.length === 0 && explanation.passed.length === 0 && explanation.unknown.length === 0) {
    return null;
  }
  return (
    <div className="stage__reasons">
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
    </div>
  );
}

function checkTone(status: string): "ok" | "bad" | "warn" {
  if (status === "hit") return "bad";
  if (status === "source_unavailable" || status === "error") return "warn";
  return "ok";
}

type PassportCheck = components["schemas"]["PassportCheckOut"];

const PASSPORT_TONE: Record<string, "ok" | "bad" | "warn"> = { ok: "ok", fail: "bad", warn: "warn", unknown: "warn" };
const TONE_ICON = { ok: "✓", bad: "✕", warn: "!" } as const;

// T23: паспорт виден в карточке всегда, а не только при отказе. Одна строка
// с итогом; по кнопке — что именно проверено и результат каждого пункта.
function PassportRow({ items }: { items: PassportCheck[] }) {
  const [open, setOpen] = useState(false);
  const tone = items.some((i) => i.status === "fail")
    ? "bad"
    : items.some((i) => i.status !== "ok")
      ? "warn"
      : "ok";
  const problems = items.filter((i) => i.status !== "ok").length;
  const summary =
    tone === "ok"
      ? `в порядке · ${items.length} из ${items.length}`
      : tone === "bad"
        ? "нарушение"
        : `замечаний: ${problems}`;
  return (
    <li className={`checklist__item checklist__item--${tone} passport-row`}>
      <span className="checklist__icon" aria-hidden="true">
        {TONE_ICON[tone]}
      </span>
      <span className="checklist__source">Паспорт</span>
      <span className="checklist__result">{summary}</span>
      <button
        type="button"
        className="btn-link passport-row__toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? "скрыть" : "что проверили"}
      </button>
      {open && (
        <ul className="passport-row__items">
          {items.map((item) => {
            const itemTone = PASSPORT_TONE[item.status] ?? "warn";
            return (
              <li key={item.key} className={`checklist__item checklist__item--${itemTone}`}>
                <span className="checklist__icon" aria-hidden="true">
                  {TONE_ICON[itemTone]}
                </span>
                <span>{passportCheckLabel(item.key)}</span>
                <span className="checklist__result">{passportCheckStatusLabel(item.status)}</span>
              </li>
            );
          })}
        </ul>
      )}
    </li>
  );
}

function CheckList({
  checks,
  canRetry,
  retry,
  busy,
  leading = null,
}: {
  checks: CheckRow[];
  canRetry: boolean;
  retry: (checkId: string) => void;
  busy: boolean;
  /** Строка перед проверками источников (T23: паспорт стадии А). */
  leading?: React.ReactNode;
}) {
  if (checks.length === 0 && !leading) return <p className="stage__reason muted">Проверок пока нет.</p>;
  return (
    <ul className="checklist">
      {leading}
      {checks.map((c) => (
        <li key={c.id} className={`checklist__item checklist__item--${checkTone(c.status)}`}>
          <span className="checklist__icon" aria-hidden="true">
            {checkTone(c.status) === "ok" ? "✓" : checkTone(c.status) === "bad" ? "✕" : "!"}
          </span>
          <span className="checklist__source">
            <SourceBadge source={c.source} />
            {c.subject_type === "guarantor" && <span className="muted"> · поручитель</span>}
          </span>
          <span className="checklist__result">{checkResultLabel(c.source, c.status)}</span>
          {c.can_retry && canRetry && (
            <button className="btn btn-small" disabled={busy} onClick={() => retry(c.id)}>
              Повторить
            </button>
          )}
        </li>
      ))}
    </ul>
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
