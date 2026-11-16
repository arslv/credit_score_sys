import { useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { useAuth } from "../auth/AuthProvider";
import { AiAnalysis } from "../components/AiAnalysis";
import { Badge } from "../components/Badge";
import { ChecksOverview, subjectKey } from "../components/ChecksOverview";
import { ClientRatingPanel } from "../components/ClientRating";
import { FedresursPanel } from "../components/FedresursPanel";
import { BkiRequestPanel } from "../components/BkiRequestPanel";
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
  subjectTypeLabel,
} from "../lib/labels";

type CheckRow = components["schemas"]["CheckOut"];

// Проверка из подробного отчёта бюро — рядом с нашей, с этой пометкой; наша
// проверка — без пометки (решение владельца: обе видны, в решение входит наша).
function BkiTag() {
  return (
    <span className="bki-tag" title="Проверка из отчёта бюро кредитных историй, не наша">
      БКИ
    </span>
  );
}


// Статусы, которые просто повторяют последнее решение системы.
const STATUS_MIRRORS_DECISION = new Set(["approved", "gray_zone", "rejected_a", "rejected_b", "to_operator"]);

function verdictTone(outcome: string | undefined): string {
  if (outcome === "approve") return "pf-verdict--ok";
  if (outcome === "reject") return "pf-verdict--bad";
  if (outcome === "manual_review") return "pf-verdict--warn";
  return "pf-verdict--wait";
}

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
    // Отчёт БКИ заказан — решение придёт фоновой задачей, обновляем карточку.
    // Заказан отчёт БКИ — опрашиваем, пока не придёт итог (заявка при этом
    // успевает побыть и в awaiting_bki, и в stage_b — pending учитывает оба).
    refetchInterval: (q) => (q.state.data?.bki_order?.pending ? 3000 : false),
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


  const visibleChecks = app.checks.filter((c) => c.source !== "mvd");
  const bureauChecks = app.bureau_checks ?? [];
  const bureauLatest = bureauChecks.filter(
    (b, i) => !bureauChecks.slice(i + 1).some((n) => n.source === b.source && n.subject_type === b.subject_type),
  );
  const bureauFor = (source: string) => bureauLatest.filter((b) => b.source === source);

  // T23 (по отзыву): отдельного списка дел нет. Что требует действия здесь —
  // в закреплённом блоке «Нужно ваше действие» с самими проверками; что
  // делается не здесь (серая зона в CRM, проверка по перечням) — строкой под
  // решением.
  const manualSubjects: { type: "applicant" | "guarantor"; id: string | null; label: string }[] = [
    { type: "applicant", id: null, label: "заявителя" },
    ...(app.has_guarantor && app.guarantor_id
      ? [{ type: "guarantor" as const, id: app.guarantor_id, label: "поручителя" }]
      : []),
  ];
  // ФССП пройден — есть результат (найдено / не найдено); недоступность сайта
  // или ошибка — не пройден. Банкротство — есть отметка сотрудника.
  const fsspPassed = (c: CheckRow | null) => c?.status === "ok" || c?.status === "hit";
  const manualState = manualSubjects.map((s) => {
    const fssp = findLatestCheck("fssp", s.type, s.id);
    const fed = findLatestCheck("fedresurs", s.type, s.id);
    return { ...s, fssp, fed, fsspDone: fsspPassed(fssp), fedDone: Boolean(fed) };
  });
  const fsspLeft = manualState.filter((s) => !s.fsspDone).map((s) => s.label);
  const fedLeft = manualState.filter((s) => !s.fedDone).map((s) => s.label);
  const grayStageB = latestDecision?.outcome === "manual_review" && latestDecision.stage === "B";
  // Заведена вручную в админке — в CRM её нет, решения туда не уходят.
  const manual = app.source === "manual";
  // Серая зона «ФССП не пройдена» — действие нужно, пока ФССП у кого-то не
  // пройдена; прошли — решение не пересчитывается само (новое — после
  // повторного «Рассмотреть» в CRM), «требуется действие» снимаем.
  const fsspGray = grayStageB && latestDecision?.reason_code === "FSSP_NOT_CHECKED";
  const fsspNeedsAction = (fsspGray && fsspLeft.length > 0) || app.fssp_post_decision_alert_subjects.length > 0;
  const fsspDoneAfterGray = fsspGray && fsspLeft.length === 0;
  const grayInCrm = grayStageB && !fsspGray;
  // Банкротство проверяется вручную — пока по заявителю нет отметки и заявка
  // ещё в работе (после отказа или истечения — незачем).
  const fedresursNeedsAction =
    fedLeft.length > 0 && app.decisions.length > 0 && !["rejected_a", "rejected_b", "expired"].includes(app.status);
  // Стадия Б ждёт, пока сотрудник закажет отчёт БКИ (запрос платный).
  const bkiWaiting = app.status === "awaiting_bki";
  // Отчёт БКИ не получен (бюро не подключено, недоступно…) — серая зона, но
  // запрос можно повторить теми же кнопками.
  const bkiRetry = app.bki_request_state === "retry";
  // После отказа или истечения ручные проверки уже ничего не меняют — блок
  // скрыт (результаты, если были, остаются в «Что проверили»). Проверки, что
  // ждут действия, показаны выше в «Нужно ваше действие».
  const manualRelevant = !["rejected_a", "rejected_b", "expired"].includes(app.status);
  const showManual = manualRelevant || fsspNeedsAction || fedresursNeedsAction;

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
    if (typeof inn === "string" && inn) return inn;
    // Наш ФНС ИНН не нашёл — берём из подробного отчёта бюро (только заявитель).
    if (subjectType !== "applicant") return null;
    const fromBureau = bureauFor("fns_inn")[0]?.parsed?.inn;
    return typeof fromBureau === "string" && fromBureau ? fromBureau : null;
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

  // Ручные проверки (16.11.2026) — не отдельной карточкой, а кнопками под
  // своей строкой в «Проверках» → «Долги и банкротство». После отказа или
  // истечения они ничего не меняют — кнопок нет.
  const rowActions: Record<string, ReactNode> = {};
  if (showManual) {
    for (const s of manualState) {
      const isGuarantor = s.type === "guarantor";
      rowActions[subjectKey("fssp", s.type, s.id)] = (
        <FsspPanel
          applicationId={app.id}
          subjectType={s.type}
          subjectId={s.id}
          latestCheck={s.fssp}
          canOperate={canOperateFssp}
          hasPostDecisionAlert={app.fssp_post_decision_alert_subjects.includes(s.type)}
        />
      );
      rowActions[subjectKey("fedresurs", s.type, s.id)] = (
        <FedresursPanel
          applicationId={app.id}
          subjectType={s.type}
          subjectId={s.id}
          fullName={isGuarantor ? guarantorName || null : (app.client_name ?? null)}
          birthDate={
            isGuarantor ? birthText(app.guarantor_profile?.birth_date as string | undefined) : birthText(birthDate)
          }
          inn={innOf(s.type, s.id)}
          address={
            isGuarantor
              ? ((app.guarantor_profile?.registration_address as string | undefined) ?? null)
              : (ocrValue(app.ocr_result?.fields ?? null, "registration_address") ?? null)
          }
          latestCheck={s.fed}
          canOperate={canOperateFssp}
        />
      );
    }
  }

  const lastOverride = app.manual_overrides.length > 0 ? app.manual_overrides[app.manual_overrides.length - 1]! : null;
  // Оператор CRM уже решил по последнему решению системы (одобрил / отказал)
  // — сверху показываем его итог и цвет, решение системы — строкой ниже.
  const operatorFinal: "approve" | "reject" | null =
    lastOverride && (!latestDecision || lastOverride.created_at >= latestDecision.created_at)
      ? ["approved", "approve"].includes(lastOverride.to_outcome)
        ? "approve"
        : ["rejected", "reject"].includes(lastOverride.to_outcome)
          ? "reject"
          : null
      : null;
  const headerOutcome = operatorFinal ?? latestDecision?.outcome;

  // Редизайн 16.11.2026 (решение владельца): сверху — кто клиент и решение
  // с причиной (одно место), кнопки. Ниже — кредитная история, ручные
  // проверки, рейтинг клиента. Стадии, ряд цифр и ход заявки убраны. Анкета, сделка, проверки таблицей,
  // документы и история — свёрнутыми разделами в самом низу.
  return (
    <div className={headerOutcome === "manual_review" ? "ac2 ac2--gray" : "ac2"}>
      <p className="back-link">
        <Link to="/applications">← Заявки</Link>
      </p>

      <section className={`ah ${verdictTone(headerOutcome)}`} aria-label="Главное по заявке">
        <div className="ah__top">
          <div className="ah__who">
            <div className="ah__avatar" aria-hidden="true">
              {initials(app.client_name)}
            </div>
            <div className="ah__who-text">
              <h2 className="ah__name">{app.client_name ?? "Клиент скрыт"}</h2>
              <p className="ah__line">
                {[age != null ? `${age} лет` : null, (app.profile?.phone as string | undefined) ?? null]
                  .filter(Boolean)
                  .join(" · ") || "паспортных данных нет"}
              </p>
              <p className="ah__line ah__line--muted">
                Заявка №{app.number} · подана {formatDateTime(app.submitted_at)} ·{" "}
                {manual ? "заведена вручную, в CRM её нет" : `в CRM ${app.external_id}`}
              </p>
              <p className="ah__line">
                {app.goods_name && app.goods_name !== "—" ? app.goods_name : "товар уточняется"}
                {app.term_months > 0 && ` · ${formatAmount(app.requested_amount)} ₽ на ${app.term_months} мес.`}
              </p>
              <div className="ah__tags">
                {innFound && <span className="pill pill-ok">ИНН найден</span>}
                {app.has_guarantor ? (
                  <span className="pill pill-mute">есть поручитель</span>
                ) : guarantorNeeded ? (
                  <span className="pill pill-bad">нужен поручитель</span>
                ) : null}
              </div>
            </div>
          </div>

          <div className="ah__verdict">
            <div className="pf-kicker">
              {operatorFinal ? `Решение оператора CRM · ${formatDateTime(lastOverride!.created_at)}` : "Решение"}
            </div>
            <div className="pf-verdict__title">
              <VerdictTitle
                title={
                  operatorFinal === "approve"
                    ? "Одобрено оператором"
                    : operatorFinal === "reject"
                      ? "Отказ оператора"
                      : latestDecision
                        ? decisionOutcomeLabel(latestDecision.stage, latestDecision.outcome)
                        : "В обработке"
                }
                help={operatorFinal ? null : (explanation?.help ?? null)}
              />
              {/* Статус дублирует решение («Серая зона» дважды) — показываем,
                  только когда он говорит что-то своё (истекла, проверяется…). */}
              {!STATUS_MIRRORS_DECISION.has(app.status) && (
                <Badge value={app.status} label={applicationStatusLabel(app.status)} />
              )}
            </div>
            {/* Причина — на момент решения. ФССП с тех пор пройдена — так и
                пишем, а не «не пройдена», как было тогда. */}
            {operatorFinal && latestDecision ? (
              <>
                {lastOverride?.comment && <p className="pf-verdict__reason">Оператор: {lastOverride.comment}</p>}
                <p className="pf-verdict__reason">
                  Система: {decisionOutcomeLabel(latestDecision.stage, latestDecision.outcome).toLowerCase()}
                  {explanation?.reason ? ` — ${explanation.reason}` : ""}
                </p>
              </>
            ) : fsspDoneAfterGray ? (
              <p className="pf-verdict__reason">
                На момент решения ФССП не была пройдена. Сейчас пройдена — новое решение будет при следующей
                проверке заявки.
              </p>
            ) : (
              explanation?.reason && <p className="pf-verdict__reason">{explanation.reason}</p>
            )}
            {grayInCrm && !operatorFinal && (
              <p className="verdict-action">
                {manual ? "Решение — за сотрудником: заявки нет в CRM." : "Решение принимает оператор в CRM."}
              </p>
            )}
            {latestDecision?.freeze && (
              <p className="verdict-action">
                Похож на человека из перечня Росфинмониторинга — проверьте, он ли это.
                <Link className="btn btn-primary btn-small" to="/compliance">
                  К проверке по перечням
                </Link>
              </p>
            )}
          </div>

          {/* «Принять» / «Отказать» — будущая отправка решения в CRM по API.
              Пока недоступны: серые, не нажимаются. */}
          {canOperateFssp && (
            <div className="ah__actions">
              <Link className="btn" to={`/applications/${app.id}/edit`}>
                Изменить анкету
              </Link>
              <div className="decision-stub__row" title="Пока недоступно — появится позже">
                <button type="button" className="btn decision-stub__btn" disabled>
                  Принять
                </button>
                <button type="button" className="btn decision-stub__btn" disabled>
                  Отказать
                </button>
              </div>
            </div>
          )}
        </div>

      </section>

      {/* Всё, что проверено по заявке, — на одном экране (решение владельца 16.11.2026). */}
      <ChecksOverview
        passport={app.passport_checks}
        checks={app.checks}
        bureau={bureauChecks}
        guarantorId={app.guarantor_id ?? null}
        guarantorName={guarantorName || null}
        bki={app.bki_result ?? null}
        bkiAction={
          bkiWaiting || bkiRetry || app.bki_order?.pending ? (
            <BkiRequestPanel
              applicationId={app.id}
              types={app.bki_report_types ?? []}
              order={app.bki_order}
              canOperate={canOperateFssp}
              retry={bkiRetry}
            />
          ) : null
        }
        rowActions={rowActions}
      />

      {/* Место под ответ ИИ по заявке — модуль в разработке (решение владельца 16.11.2026). */}
      <AiAnalysis />

      {app.client_rating && <ClientRatingPanel rating={app.client_rating} />}

      {/* Всё вспомогательное — ниже и свёрнуто: открывают, когда нужно. */}
      <div className="more">
        <h2 className="more__title">Данные заявки</h2>
        {retryMutation.isError && <p role="alert" className="alert">Не удалось повторить проверку.</p>}

        <details className="more__item">
          <summary>
            <h2 className="more__name">Анкета клиента</h2>
            <span className="more__hint">паспорт, адрес, работа, доход{app.guarantor_profile ? ", поручитель" : ""}</span>
          </summary>
          <div className="more__body">
            {profileRows.length > 0 ? <KeyValue rows={profileRows} /> : <p className="muted">Анкеты пока нет.</p>}
            {app.guarantor_profile && (
              <>
                <h3 className="subhead">Поручитель</h3>
                <KeyValue
                  rows={Object.entries(app.guarantor_profile)
                    .filter(([, value]) => value != null && value !== "")
                    .map(([field, value]) => ({
                      label: humanizeFieldPath(field),
                      value: field.endsWith("_date") ? formatDate(String(value)) : String(value),
                    }))}
                />
              </>
            )}
          </div>
        </details>

        <details className="more__item">
          <summary>
            <h2 className="more__name">Условия сделки</h2>
            <span className="more__hint">
              {dealBlock.length > 0 ? "товар, взнос, срок и тариф, платёж" : "появятся, когда CRM пришлёт сделку"}
            </span>
          </summary>
          <div className="more__body">
            {dealBlock.length > 0 ? <KeyValue rows={dealBlock} /> : <p className="muted">Сделка ещё не оформлена в CRM.</p>}
          </div>
        </details>

        <details className="more__item">
          <summary>
            <h2 className="more__name">Что проверили</h2>
            <span className="more__hint">все проверки и повторы одной таблицей · {visibleChecks.length + bureauChecks.length}</span>
          </summary>
          <div className="more__body">
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
                    <td className="muted">
                      {c.error_text ?? "—"}
                      {c.can_retry && canRetry && (
                        <button
                          className="btn btn-small checks-retry"
                          disabled={retryMutation.isPending}
                          onClick={() => retry(c.id)}
                        >
                          Повторить
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
                {bureauChecks.map((b) => (
                  <tr key={b.id}>
                    <td>
                      <SourceBadge source={b.source} /> <BkiTag />
                    </td>
                    <td className="muted">{subjectTypeLabel(b.subject_type)}</td>
                    <td>
                      {checkResultLabel(b.source, b.status)}
                      {typeof b.parsed?.summary === "string" && <span className="muted"> — {b.parsed.summary}</span>}
                    </td>
                    <td className="muted">{formatDateTime(b.created_at)}</td>
                    <td className="muted">—</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>

        <details className="more__item">
          <summary>
            <h2 className="more__name">Документы</h2>
            <span className="more__hint">фото паспорта · {app.documents.length}</span>
          </summary>
          <div className="more__body">
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
          </div>
        </details>

        <details className="more__item">
          <summary>
            <h2 className="more__name">История</h2>
            <span className="more__hint">смена статусов · {app.status_history.length}</span>
          </summary>
          <div className="more__body">
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
          </div>
        </details>

        {/* Сырьё (T17, часть 2): паспортные поля и полные снимки анкеты. */}
        <details className="more__item disclosure">
          <summary>
            <span className="more__name">Подробности</span>
            <span className="more__hint">паспортные данные и анкеты из CRM как есть</span>
          </summary>
          <div className="more__body">
            {app.ocr_result ? (
              <>
                <h3 className="subhead">
                  Паспортные данные · мин. уверенность {app.ocr_result.min_confidence ?? "—"} ·{" "}
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
                    {Object.entries((app.ocr_result.fields.fields as Record<string, unknown>) ?? {}).map(([field, value]) => {
                      const v = value as { value?: unknown; confidence?: number };
                      return (
                        <tr key={field}>
                          <td>{humanizeFieldPath(field)}</td>
                          <td>{String(v.value ?? "—")}</td>
                          <td className="muted">{v.confidence ?? "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </>
            ) : null}
            <h3 className="subhead">Снимки анкеты</h3>
            <SnapshotHistory snapshots={app.external_snapshots} />
          </div>
        </details>
      </div>
    </div>
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
