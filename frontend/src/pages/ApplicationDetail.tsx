import { useState } from "react";
import type React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { useAuth } from "../auth/AuthProvider";
import { Badge } from "../components/Badge";
import { ClientRatingPanel } from "../components/ClientRating";
import { FedresursPanel } from "../components/FedresursPanel";
import { FsspPanel } from "../components/FsspPanel";
import { SnapshotHistory } from "../components/SnapshotHistory";
import { SourceBadge } from "../components/SourceBadge";
import { VerdictTitle } from "../components/VerdictTitle";
import { ErrorState, LoadingState } from "../components/ui";
import { clientRows, dealRows, latestStageBPayload, ocrValue } from "../lib/card";
import { explainDecision } from "../lib/explain";
import { formatAmount, formatDate, formatDateTime } from "../lib/format";
import {
  actorTypeLabel,
  applicationStatusLabel,
  checkResultLabel,
  decisionOutcomeLabel,
  documentKindLabel,
  humanizeFieldPath,
  passportCheckLabel,
  passportCheckStatusLabel,
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

// T23: заголовок зоны карточки — название и подсказка, что в ней.
function ZoneHead({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="zone__head">
      <h2 className="zone__title">{title}</h2>
      <span className="zone__hint">{hint}</span>
    </div>
  );
}

// T23: «К проверке ФССП» — плавно прокручиваем к разделу и на пару секунд
// подсвечиваем его, чтобы глаз сразу нашёл, куда перешли. Класс снимается
// по окончании анимации, повторный клик подсвечивает заново.
function jumpAndFlash(event: React.MouseEvent, targetId: string) {
  const target = document.getElementById(targetId)?.closest("section");
  if (!target) return;
  event.preventDefault();
  history.replaceState(null, "", `#${targetId}`);
  target.scrollIntoView({ behavior: "smooth", block: "start" });
  target.classList.remove("section--flash");
  void target.offsetWidth; // перезапуск анимации при повторном клике
  target.classList.add("section--flash");
  target.addEventListener("animationend", () => target.classList.remove("section--flash"), { once: true });
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

  const findLatestCheck = (
    source: "fssp" | "fedresurs", subjectType: "applicant" | "guarantor", subjectId: string | null,
  ) => {
    const matches = app.checks.filter(
      (c) => c.source === source && c.subject_type === subjectType && (c.subject_id ?? null) === subjectId,
    );
    if (matches.length === 0) return null;
    return matches.reduce((latest, c) => (c.started_at > latest.started_at ? c : latest));
  };

  const latestDecision = app.decisions.length > 0 ? app.decisions[app.decisions.length - 1] : null;
  const stageB = latestStageBPayload(app.external_snapshots);

  // «Нужно сделать» — только если что-то реально требуется. Стадия А «передано
  // оператору» — не действие для нас: ждём звонка и стадии Б. T20: одна строка
  // с кнопкой сразу под вердиктом, без отдельной рамки.
  const actionItems: { key: string; text: string; href?: string; button?: string }[] = [];
  if (latestDecision?.outcome === "manual_review" && latestDecision.stage === "B") {
    if (latestDecision.reason_code === "FSSP_NOT_CHECKED") {
      actionItems.push({ key: "fssp", text: "Проверить ФССП — без этого одобрение недоступно", href: "#fssp", button: "К проверке ФССП" });
    } else {
      actionItems.push({ key: "gray", text: "Серая зона — решение принимает оператор в системе заказчика" });
    }
  }
  if (latestDecision?.freeze) {
    actionItems.push({
      key: "freeze",
      text: "Похож на человека из перечня Росфинмониторинга — проверьте, он ли это",
      href: "/compliance",
      button: "К проверке по перечням",
    });
  }
  // T23: банкротство проверяется вручную — напоминаем, пока по заявителю нет
  // отметки и заявка ещё в работе (после отказа или истечения — незачем).
  const applicantFedresurs = findLatestCheck("fedresurs", "applicant", null);
  if (
    !applicantFedresurs &&
    app.decisions.length > 0 &&
    !["rejected_a", "rejected_b", "expired"].includes(app.status)
  ) {
    actionItems.push({
      key: "fedresurs",
      text: "Проверить банкротство на Федресурсе",
      href: "#fedresurs",
      button: "К проверке банкротства",
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

  // T23 (правка по отзыву): стадии А и Б — отдельными карточками под шкалой
  // «Путь заявки»; рейтинг клиента — цветной карточкой сразу под вердиктом;
  // анкета, полная таблица проверок и история — по кнопке (<details>, содержимое
  // остаётся в DOM — поиск по странице и тесты видят его и в свёрнутом виде).
  const decisionsA = app.decisions.filter((d) => d.stage === "A");
  const decisionsB = app.decisions.filter((d) => d.stage === "B");
  const latestA = decisionsA.length > 0 ? decisionsA[decisionsA.length - 1] : null;
  const latestB = decisionsB.length > 0 ? decisionsB[decisionsB.length - 1] : null;
  const rejectedAtA = latestA?.outcome === "reject" && !latestB;
  const reviewSnapshots = app.external_snapshots.filter((s) => s.stage === "B");
  const reviewedAt = reviewSnapshots.length > 0 ? reviewSnapshots[reviewSnapshots.length - 1]!.received_at : null;
  const callDone = Boolean(latestB) || reviewedAt != null;

  const visibleChecks = app.checks.filter((c) => c.source !== "mvd");
  // Ручные проверки (ФССП, Федресурс) — после звонка, в карточке стадии Б.
  const isManual = (c: CheckRow) => c.source === "fssp" || c.source === "fedresurs";
  const checksA = visibleChecks.filter((c) => c.subject_type !== "guarantor" && !isManual(c));
  const checksB = visibleChecks.filter((c) => c.subject_type === "guarantor" || isManual(c));

  // Строка для поиска на Федресурсе: ФИО и дата рождения.
  const searchTextFor = (name: string | null | undefined, birth: string | null | undefined) =>
    name ? [name, birth ? formatDate(birth) : null].filter(Boolean).join(" ") : null;
  const guarantorName = app.guarantor_profile
    ? [app.guarantor_profile.last_name, app.guarantor_profile.first_name, app.guarantor_profile.middle_name]
        .filter((part) => typeof part === "string" && part)
        .join(" ")
    : null;

  const steps: { label: string; date: string | null; state: StepState }[] = [
    { label: "Подача", date: app.submitted_at, state: "done" },
    {
      label: "Стадия А",
      date: latestA?.created_at ?? null,
      state: latestA ? (latestA.outcome === "reject" ? "bad" : "done") : "current",
    },
    {
      label: "Звонок оператора",
      date: reviewedAt,
      state: callDone ? "done" : rejectedAtA ? "skipped" : latestA ? "current" : "pending",
    },
    {
      label: "Стадия Б",
      date: latestB?.created_at ?? null,
      state: latestB
        ? latestB.outcome === "approve" ? "done" : latestB.outcome === "reject" ? "bad" : "gray"
        : app.status === "stage_b" ? "current" : rejectedAtA ? "skipped" : "pending",
    },
  ];

  const applicantFssp = findLatestCheck("fssp", "applicant", null);
  const guarantorNeeded = (app.client_rating?.guarantor_reasons.length ?? 0) > 0;
  const facts: { label: string; value: string; hint?: string; tone?: Tone }[] = [
    {
      label: "ПДН",
      value: latestB?.pti != null ? `${Math.round(latestB.pti * 100)}%` : "—",
      hint: latestB?.pti != null && latestB.pti_max != null ? `порог отказа ${Math.round(latestB.pti_max * 100)}%` : "после стадии Б",
      tone: latestB?.pti != null && latestB.pti_max != null ? (latestB.pti > latestB.pti_max ? "bad" : "ok") : undefined,
    },
    applicantFssp
      ? {
          label: "ФССП",
          value: checkResultLabel("fssp", applicantFssp.status),
          hint: formatDateTime(applicantFssp.started_at),
          tone: applicantFssp.status === "hit" ? "bad" : "ok",
        }
      : rejectedAtA
        ? { label: "ФССП", value: "не нужна", hint: "отказ на стадии А" }
        : { label: "ФССП", value: "не проверено", hint: "без проверки одобрение недоступно", tone: "gray" },
    {
      label: "Поручитель",
      value: app.has_guarantor ? "есть" : guarantorNeeded ? "нужен, нет" : "не нужен",
      hint: guarantorNeeded ? "по условиям заказчика" : undefined,
      tone: guarantorNeeded && !app.has_guarantor ? "bad" : app.has_guarantor ? "ok" : undefined,
    },
    {
      label: "Сумма и срок",
      value: app.term_months > 0 ? `${formatAmount(app.requested_amount)} ₽` : "—",
      hint: app.term_months > 0 ? `на ${app.term_months} мес.` : "после «Рассмотреть»",
    },
  ];

  return (
    <section>
      <p className="back-link">
        <Link to="/applications">← к списку заявок</Link>
      </p>

      {/* T23: карточка — три зоны: решение (сведения), что нужно сделать,
          анкета и документы. Зоны — div, не section: переход «К проверке …»
          подсвечивает ближайший section, то есть сам раздел, а не всю зону. */}
      <div className="zone zone--info">
        <ZoneHead title="Решение" hint="Что решила система и почему" />
        <div className={`verdict ${decisionVerdictClass(latestDecision?.outcome)}`}>
          <div className="verdict-main">
            <div className="verdict-kicker">
              {latestDecision ? `Решение системы · стадия ${stageLabel(latestDecision.stage)}` : "Заявка в обработке"}
            </div>
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
            </p>
          </div>
          {latestDecision?.score != null && (
            <div className="verdict-score">
              <div className="verdict-score-value">
                {wholeNumber(latestDecision.score)} <span className="unit">баллов БКИ</span>
              </div>
              {latestDecision.score_auto_approve_above != null && latestDecision.score_auto_reject_below != null && (
                <>
                  {/* Зоны шкалы — отказ / серая / одобрение по порогам ЭТОГО решения. */}
                  <div
                    className="score-scale"
                    style={
                      {
                        "--reject-at": `${(Number(latestDecision.score_auto_reject_below) / 999) * 100}%`,
                        "--approve-at": `${(Number(latestDecision.score_auto_approve_above) / 999) * 100}%`,
                      } as React.CSSProperties
                    }
                  >
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

        <div className={app.client_rating ? "card-top" : "card-top card-top--facts-only"}>
          {app.client_rating && <ClientRatingPanel rating={app.client_rating} />}
          <ul className="facts">
            {facts.map((f) => (
              <li key={f.label} className={f.tone ? `fact fact--${f.tone}` : "fact"}>
                <span className="fact__label">{f.label}</span>
                <span className="fact__value">{f.value}</span>
                {f.hint && <span className="fact__hint">{f.hint}</span>}
              </li>
            ))}
          </ul>
        </div>

        <section className="section">
          <h2>Путь заявки</h2>
          <ol className="stepper">
            {steps.map((s, i) => (
              <li key={s.label} className={`stepper__step stepper__step--${s.state}`}>
                <span className="stepper__dot" aria-hidden="true">
                  {s.state === "done" ? "✓" : s.state === "bad" ? "✕" : s.state === "gray" ? "!" : i + 1}
                </span>
                <span className="stepper__text">
                  <span className="stepper__label">{s.label}</span>
                  <span className="stepper__date">
                    {s.date ? formatDateTime(s.date) : STEP_STATE_TEXT[s.state]}
                  </span>
                </span>
              </li>
            ))}
          </ol>

          <div className="stages">
            <article className={`stage stage--${stageTone(latestA)}`}>
              <header className="stage__head">
                <div>
                  <div className="stage__name">Стадия А</div>
                  <div className="stage__when">сразу после подачи · паспорт и реестры, без БКИ</div>
                </div>
                <StageOutcome decision={latestA} pending="в обработке" />
              </header>
              {latestDecision?.stage === "A" && explanation ? (
                <DecisionReasons explanation={explanation} />
              ) : (
                latestA?.reason_text && <p className="stage__reason">{latestA.reason_text}</p>
              )}
              <CheckList
                checks={checksA}
                canRetry={canRetry}
                retry={(checkId) => retryMutation.mutate(checkId)}
                busy={retryMutation.isPending}
                leading={app.passport_checks.length > 0 ? <PassportRow items={app.passport_checks} /> : null}
              />
            </article>

            <article className={`stage stage--${latestB ? stageTone(latestB) : rejectedAtA ? "skipped" : "pending"}`}>
              <header className="stage__head">
                <div>
                  <div className="stage__name">Стадия Б</div>
                  <div className="stage__when">после звонка оператора · БКИ, долговая нагрузка, балл</div>
                </div>
                <StageOutcome
                  decision={latestB}
                  pending={rejectedAtA ? "не проводилась" : app.status === "stage_b" ? "идёт" : "ждёт звонка оператора"}
                />
              </header>
              {latestDecision?.stage === "B" && explanation ? (
                <DecisionReasons explanation={explanation} />
              ) : (
                <p className="stage__reason muted">
                  {rejectedAtA
                    ? "Заявка отклонена на стадии А — до звонка и БКИ дело не дошло."
                    : "Начнётся, когда оператор заказчика позвонит клиенту и нажмёт «Рассмотреть»."}
                </p>
              )}
              {decisionsB.length > 1 && (
                <p className="stage__note">
                  Стадию Б проходили {decisionsB.length} раза — показано последнее решение. Ранее:{" "}
                  {decisionsB
                    .slice(0, -1)
                    .map((d) => `${decisionOutcomeLabel(d.stage, d.outcome)} (${formatDateTime(d.created_at)})`)
                    .join(", ")}
                  .
                </p>
              )}
              {checksB.length > 0 && (
                <CheckList checks={checksB} canRetry={canRetry} retry={(checkId) => retryMutation.mutate(checkId)} busy={retryMutation.isPending} />
              )}
            </article>
          </div>
          {retryMutation.isError && <p role="alert" className="alert">Не удалось повторить проверку.</p>}
        </section>

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

      </div>

      <div className="zone zone--action">
        <ZoneHead title="Нужно сделать" hint="Действия по заявке и ручные проверки" />
        {/* T23: «Нужно сделать» — список дел по заявке с переходом к месту, где это делается. */}
        {actionItems.length > 0 ? (
          <ul className="todo-list">
            {actionItems.map((item) => (
              <li className="next-step" key={item.key}>
                <span className="next-step__text">{item.text}</span>
                {item.href && (
                  <a
                    className="btn btn-primary btn-small"
                    href={item.href}
                    onClick={item.href.startsWith("#") ? (e) => jumpAndFlash(e, item.href!.slice(1)) : undefined}
                  >
                    {item.button ?? "Открыть"}
                  </a>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="todo-empty">Сейчас по заявке действий не требуется.</p>
        )}

        <section className="section">
          <h2 id="fssp">ФССП</h2>
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

        {/* T23: банкротство — сразу под ФССП, тоже ручная проверка. */}
        <section className="section">
          <h2 id="fedresurs">Банкротство (Федресурс)</h2>
          <FedresursPanel
            applicationId={app.id}
            subjectType="applicant"
            subjectLabel="заявитель"
            subjectId={null}
            searchText={searchTextFor(app.client_name, ocrValue(app.ocr_result?.fields ?? null, "birth_date"))}
            latestCheck={applicantFedresurs}
            canOperate={canOperateFssp}
          />
          {app.has_guarantor && app.guarantor_id && (
            <FedresursPanel
              applicationId={app.id}
              subjectType="guarantor"
              subjectLabel="поручитель"
              subjectId={app.guarantor_id}
              searchText={searchTextFor(guarantorName, app.guarantor_profile?.birth_date as string | undefined)}
              latestCheck={findLatestCheck("fedresurs", "guarantor", app.guarantor_id)}
              canOperate={canOperateFssp}
            />
          )}
        </section>

      </div>

      <div className="zone zone--data">
        <ZoneHead title="Анкета и документы" hint="Данные клиента, все проверки, история" />
        {(clientBlock.length > 0 || dealBlock.length > 0 || app.guarantor_profile) && (
          <section className="section">
            <h2>Клиент и сделка</h2>
            <p className="muted">
              {[app.client_name ?? "Клиент скрыт", app.goods_name !== "—" ? app.goods_name : null, app.goods_category]
                .filter(Boolean)
                .join(" · ")}
            </p>
            <details className="fold">
              <summary>Показать анкету</summary>
              <div className="section-columns">
                {clientBlock.length > 0 && (
                  <div className="subsection">
                    <h3 className="subhead">Клиент</h3>
                    <KeyValue rows={clientBlock} />
                  </div>
                )}
                {dealBlock.length > 0 && (
                  <div className="subsection">
                    <h3 className="subhead">Сделка</h3>
                    <KeyValue rows={dealBlock} />
                  </div>
                )}
              </div>
            </details>
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
          </section>
        )}

        <section className="section">
          <h2>Что проверили</h2>
          <p className="muted">По стадиям — выше, в карточках стадий.</p>
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

        <section className="section">
          <h2>Документы</h2>
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

        <section className="section">
          <h2>История</h2>
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
      </div>
    </section>
  );
}

type Tone = "ok" | "bad" | "gray";
type StepState = "done" | "bad" | "gray" | "current" | "pending" | "skipped";
type Decision = components["schemas"]["DecisionOut"];
type CheckRow = components["schemas"]["CheckOut"];

const STEP_STATE_TEXT: Record<StepState, string> = {
  done: "",
  bad: "",
  gray: "",
  current: "сейчас",
  pending: "ещё впереди",
  skipped: "не понадобилась",
};

function stageTone(decision: Decision | null): string {
  if (!decision) return "pending";
  if (decision.outcome === "approve") return "ok";
  if (decision.outcome === "reject") return "bad";
  return decision.stage === "B" ? "gray" : "neutral";
}

function StageOutcome({ decision, pending }: { decision: Decision | null; pending: string }) {
  if (!decision) return <span className="stage__outcome stage__outcome--pending">{pending}</span>;
  return (
    <span className={`stage__outcome stage__outcome--${stageTone(decision)}`}>
      {decisionOutcomeLabel(decision.stage, decision.outcome)}
      <span className="stage__outcome-date">{formatDateTime(decision.created_at)}</span>
    </span>
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
