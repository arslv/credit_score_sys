"""T12: заявки — список и карточка, только просмотр (решения принимаются в
системе заказчика, ТЗ «Карточка заявки»). Персданные — только risk_manager и
compliance; admin и viewer получают обезличенный вид (ваше уточнение: admin
для своей работы — сотрудники/справочники/аудит/состояние системы — паспорта
заявителей ему не нужны)."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.engine.pipeline import _subject_from_ocr, unwrap_ocr_fields
from app.engine.rules import resolve_matrix_overrides
from app.engine.versions import load_config
from app.integrations.status_mapping import _render_rule_text, build_operator_comment, get_all_reason_notes
from app.models import (
    Application,
    BkiReport,
    Check,
    Client,
    Decision,
    Document,
    Employee,
    ExternalSnapshot,
    FeatureSnapshot,
    FreezeLift,
    FsspPostDecisionAlert,
    GoodsCategory,
    Guarantor,
    ManualOverride,
    OcrResult,
    RulesetVersion,
    StatusHistory,
)
from app.security.audit import record_audit
from app.security.deps import ROLES_WITH_FULL_PII, get_current_employee, require_roles
from app.security.pagination import Page, PageParams
from app.sources.base import Subject
from app.sources.factory import build_fns_egrip_source, build_fns_inn_source, build_fns_npd_source
from app.sources.runner import record_check, run_and_record_check
from app.storage.base import Storage

router = APIRouter(prefix="/api/applications", tags=["applications"])

_MASKED_NAME = "Клиент скрыт"
# T17, часть 4 (ваше указание): оператор видит ФИО в СПИСКЕ заявок тоже (раньше —
# только в карточке). Остальные списки/экраны по-прежнему по ROLES_WITH_FULL_PII.
_LIST_PII_ROLES = (*ROLES_WITH_FULL_PII, "operator")
# Часовой пояс сервиса — для «Сегодня» (заявки за текущие сутки по Москве).
_SERVICE_TZ = ZoneInfo("Europe/Moscow")
_VIEW_ROLES = ("risk_manager", "admin", "viewer", "compliance", "operator")
_PHOTO_ROLES = ("risk_manager", "compliance")
_CREDIT_REPORT_ROLES = ("risk_manager", "compliance")
_RETRY_ROLES = ("risk_manager",)
# T16 (ваша правка): ПД в КАРТОЧКЕ заявки (не в списке, не у клиентов, не в
# симуляции правил — там has_full_pii/ROLES_WITH_FULL_PII не меняем) —
# operator нужно видеть ФИО/дату рождения/адрес регистрации заявителя и
# поручителя, чтобы выбрать регион ФССП и отличить должника из найденного
# производства от однофамильца. Фото и кредитный отчёт — не входят
# (_PHOTO_ROLES/_CREDIT_REPORT_ROLES выше не трогаем), не запрошено.
_DETAIL_PII_ROLES = (*ROLES_WITH_FULL_PII, "operator")
# Только эти источники имеют смысл повторять руками (ваше уточнение):
# МВД/Росфинмониторинг/чёрный список — локальные (БД), их "недоступность" —
# не то, что чинится нажатием кнопки; БКИ — платный запрос, повтор только
# через «Рассмотреть» оператором; ФССП (T16) — вообще не через эту кнопку,
# у него свой экран с капчей (app/api/fssp.py).
_RETRYABLE_SOURCES = ("fns_inn", "fns_npd", "fns_egrip")


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


class ApplicationListItem(BaseModel):
    id: UUID
    number: int
    external_id: str
    submitted_at: datetime
    client_name: str | None
    goods_price: Decimal
    goods_name: str
    requested_amount: Decimal
    term_months: int
    status: str
    stage_a_outcome: str | None
    stage_b_outcome: str | None
    score: Decimal | None
    has_override: bool
    override_matches_system: bool | None
    # Очередь роли (только для «Требуют действия»): вид действия (точка-цвет),
    # короткий текст и момент, с которого заявка ждёт этой роли.
    action_kind: str | None
    action_text: str | None
    waiting_since: datetime | None


async def _latest_outcome(session: AsyncSession, application_id: UUID, stage: str) -> Decision | None:
    return (
        await session.execute(
            select(Decision)
            .where(Decision.application_id == application_id, Decision.stage == stage)
            .order_by(Decision.created_at.desc())
            .limit(1)
        )
    ).scalars().first()


def _freeze_pending_clause():
    """Есть решение с заморозкой по перечню, которое комплаенс ещё не снял
    (FreezeLift привязан к конкретному решению — decision_id)."""
    return exists(
        select(Decision.id).where(
            Decision.application_id == Application.id,
            Decision.freeze.is_(True),
            ~exists(select(FreezeLift.id).where(FreezeLift.decision_id == Decision.id)),
        )
    )


def _fssp_unchecked_gray_clause():
    """Серая зона из-за непроверенной ФССП (стадия Б, reason FSSP_NOT_CHECKED)."""
    return exists(
        select(Decision.id).where(
            Decision.application_id == Application.id,
            Decision.stage == "B",
            Decision.reason_code == "FSSP_NOT_CHECKED",
        )
    )


def _latest_override_mismatch_clause():
    """Последний итог оператора не совпадает с последним решением нашего
    сервиса — «оператор изменил статус» (то же, что в _to_item: override_matches)."""
    latest_outcome = (
        select(Decision.outcome)
        .where(Decision.application_id == Application.id)
        .order_by(Decision.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )
    latest_override = (
        select(ManualOverride.id)
        .where(ManualOverride.application_id == Application.id)
        .order_by(ManualOverride.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )
    return exists(
        select(ManualOverride.id).where(
            ManualOverride.id == latest_override,
            ManualOverride.to_outcome != latest_outcome,
        )
    )


def _latest_override_at_expr():
    """Момент последнего итога оператора, если он расходится с решением."""
    latest_outcome = (
        select(Decision.outcome)
        .where(Decision.application_id == Application.id)
        .order_by(Decision.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )
    return (
        select(ManualOverride.created_at)
        .where(ManualOverride.application_id == Application.id, ManualOverride.to_outcome != latest_outcome)
        .order_by(ManualOverride.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )


# T17, часть 4 (ваше указание): у каждой роли своя очередь «Требуют действия».
# Заявка, которую роль ничего сделать не может, в её очередь не попадает.
_QUEUE_ROLES = ("operator", "compliance", "risk_manager")


def _queue_clause(role: str):
    """SQL-условие очереди роли (зеркало _queue_entry — держать в синхроне)."""
    if role == "operator":
        return or_(
            Application.status.in_(("to_operator", "ocr_failed")),
            and_(Application.status == "gray_zone", _fssp_unchecked_gray_clause()),
        )
    if role == "compliance":
        return _freeze_pending_clause()
    if role == "risk_manager":
        return or_(Application.status == "gray_zone", _latest_override_mismatch_clause())
    return None


def _latest_decision_at_expr(stage: str):
    return (
        select(func.max(Decision.created_at))
        .where(Decision.application_id == Application.id, Decision.stage == stage)
        .scalar_subquery()
    )


def _queue_waiting_expr(role: str):
    """SQL-выражение «ждёт с» для очереди роли — то же, что _queue_entry, но в
    SQL: сортировка и страница делаются в базе, а не в Python по всем заявкам."""
    status_since = (
        select(func.max(StatusHistory.created_at))
        .where(StatusHistory.application_id == Application.id, StatusHistory.to_status == Application.status)
        .scalar_subquery()
    )
    fallback = Application.submitted_at
    if role == "operator":
        expr = case(
            (Application.status.in_(("to_operator", "ocr_failed")), status_since),
            else_=_latest_decision_at_expr("B"),
        )
    elif role == "compliance":
        expr = (
            select(func.max(Decision.created_at))
            .where(
                Decision.application_id == Application.id,
                Decision.freeze.is_(True),
                ~exists(select(FreezeLift.id).where(FreezeLift.decision_id == Decision.id)),
            )
            .scalar_subquery()
        )
    elif role == "risk_manager":
        expr = case(
            (Application.status == "gray_zone", _latest_decision_at_expr("B")),
            else_=_latest_override_at_expr(),
        )
    else:
        return fallback
    return func.coalesce(expr, fallback)


def _queue_entry(
    role: str,
    status: str,
    latest_b: Decision | None,
    freeze_at: datetime | None,
    mismatch_override: ManualOverride | None,
    status_since: datetime,
) -> tuple[str, str, datetime] | None:
    """(вид действия, короткий текст, с какого момента ждёт) — если заявка в
    очереди этой роли, иначе None. Короткие тексты — для колонки списка; полные
    формулировки — в карточке."""
    if role == "operator":
        if status in ("to_operator", "ocr_failed"):
            return ("call", "Позвонить", status_since)
        if status == "gray_zone" and latest_b is not None and latest_b.reason_code == "FSSP_NOT_CHECKED":
            return ("fssp", "Проверить ФССП", latest_b.created_at)
        return None
    if role == "compliance":
        return ("compliance", "Ждёт комплаенс", freeze_at) if freeze_at is not None else None
    if role == "risk_manager":
        if status == "gray_zone":
            return ("gray", "Решение по серой зоне", latest_b.created_at if latest_b else status_since)
        if mismatch_override is not None:
            return ("override", "Проверить изменение", mismatch_override.created_at)
        return None
    return None


def _service_today_start_utc() -> datetime:
    """Начало текущих суток по Москве, в UTC — для фильтра «Сегодня»."""
    now_local = datetime.now(_SERVICE_TZ)
    return now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


@router.get("/quick-counts")
async def quick_counts(
    employee: Employee = Depends(require_roles(*_VIEW_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int | None]:
    """Счётчики быстрых фильтров списка (макет «Заявки»): тот же набор условий,
    что у самих фильтров в list_applications, — цифра на чипе совпадает со
    списком, который откроется по клику."""
    async def _count(*conditions) -> int:
        stmt = select(func.count(Application.id))
        for condition in conditions:
            stmt = stmt.where(condition)
        return (await session.execute(stmt)).scalar_one()

    queue = _queue_clause(employee.role)
    return {
        # None — у роли нет своей очереди (наблюдатель, администратор): чип не показываем.
        "action": await _count(queue) if queue is not None else None,
        "gray": await _count(Application.status == "gray_zone"),
        "today": await _count(Application.submitted_at >= _service_today_start_utc()),
        "all": await _count(),
    }


@router.get("", response_model=Page[ApplicationListItem])
async def list_applications(
    params: PageParams = Depends(),
    status_filter: str | None = Query(None, alias="status"),
    submitted_from: date | None = None,
    submitted_to: date | None = None,
    amount_min: Decimal | None = None,
    amount_max: Decimal | None = None,
    goods_category_code: str | None = None,
    has_override: bool | None = None,
    ocr_failed_only: bool = False,
    source_unavailable_only: bool = False,
    # «Расхождения с оператором» — главный инструмент риск-менеджера (ТЗ,
    # «Список заявок»): не просто «есть override» (has_override), а override,
    # который НЕ СОВПАЛ с решением системы. Сравнение по override_matches_system
    # уже не выражается плоским условием в SQL (нужен самый свежий override И
    # самое свежее решение по заявке) — материализуем кандидатов и сравниваем
    # в Python (см. цикл ниже), как уже делает total= для остальных фильтров.
    discrepancies_only: bool = False,
    # Быстрые фильтры макета: action — «Требуют действия», gray — «Серая зона»,
    # today — «Сегодня» (по Москве), all — без ограничения.
    view: str = Query("all", pattern="^(action|gray|today|all)$"),
    employee: Employee = Depends(require_roles(*_VIEW_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> Page[ApplicationListItem]:
    stmt = select(Application)
    queue = _queue_clause(employee.role) if view == "action" else None
    if view == "action":
        if queue is None:
            return Page(items=[], total=0, page=params.page, page_size=params.page_size)
        stmt = stmt.where(queue)
    elif view == "gray":
        stmt = stmt.where(Application.status == "gray_zone")
    elif view == "today":
        stmt = stmt.where(Application.submitted_at >= _service_today_start_utc())
    if status_filter:
        stmt = stmt.where(Application.status == status_filter)
    if submitted_from:
        stmt = stmt.where(Application.submitted_at >= submitted_from)
    if submitted_to:
        stmt = stmt.where(Application.submitted_at < submitted_to)
    if amount_min is not None:
        stmt = stmt.where(Application.requested_amount >= amount_min)
    if amount_max is not None:
        stmt = stmt.where(Application.requested_amount <= amount_max)
    if goods_category_code:
        stmt = stmt.where(
            Application.goods_category_id.in_(
                select(GoodsCategory.id).where(GoodsCategory.code == goods_category_code)
            )
        )
    if ocr_failed_only:
        stmt = stmt.where(Application.status == "ocr_failed")
    if discrepancies_only:
        has_override = True
    if has_override is not None:
        override_exists = exists(
            select(ManualOverride.id).where(ManualOverride.application_id == Application.id)
        )
        stmt = stmt.where(override_exists if has_override else ~override_exists)
    if source_unavailable_only:
        stmt = stmt.where(
            exists(
                select(Check.id).where(
                    Check.application_id == Application.id, Check.status == "source_unavailable"
                )
            )
        )
    stmt = stmt.order_by(Application.submitted_at.desc())

    list_pii = employee.role in _LIST_PII_ROLES

    async def _to_item(application: Application) -> tuple[ApplicationListItem, bool | None]:
        client_name = None
        if list_pii and application.client_id is not None:
            client = await session.get(Client, application.client_id)
            if client is not None:
                client_name = f"{client.last_name} {client.first_name} {client.middle_name or ''}".strip()
        elif application.client_id is not None:
            client_name = _MASKED_NAME

        decision_a = await _latest_outcome(session, application.id, "A")
        decision_b = await _latest_outcome(session, application.id, "B")
        override = (
            await session.execute(
                select(ManualOverride)
                .where(ManualOverride.application_id == application.id)
                .order_by(ManualOverride.created_at.desc())
                .limit(1)
            )
        ).scalars().first()

        freeze_at = (
            await session.execute(
                select(func.max(Decision.created_at)).where(
                    Decision.application_id == application.id,
                    Decision.freeze.is_(True),
                    ~exists(select(FreezeLift.id).where(FreezeLift.decision_id == Decision.id)),
                )
            )
        ).scalar_one()
        status_since = (
            await session.execute(
                select(func.max(StatusHistory.created_at)).where(
                    StatusHistory.application_id == application.id,
                    StatusHistory.to_status == application.status,
                )
            )
        ).scalar_one() or application.submitted_at

        latest_decision = decision_b or decision_a
        override_matches = None
        if override is not None and latest_decision is not None:
            override_matches = override.to_outcome == latest_decision.outcome
        mismatch_override = override if override_matches is False else None
        entry = _queue_entry(
            employee.role, application.status, decision_b, freeze_at, mismatch_override, status_since,
        )

        item = ApplicationListItem(
            id=application.id, number=application.number, external_id=application.external_id,
            submitted_at=application.submitted_at, client_name=client_name,
            goods_price=application.goods_price, goods_name=application.goods_name,
            requested_amount=application.requested_amount, term_months=application.term_months,
            status=application.status,
            stage_a_outcome=decision_a.outcome if decision_a else None,
            stage_b_outcome=decision_b.outcome if decision_b else None,
            score=(decision_b.score if decision_b else None),
            has_override=override is not None,
            override_matches_system=override_matches,
            action_kind=entry[0] if entry else None,
            action_text=entry[1] if entry else None,
            waiting_since=entry[2] if entry else None,
        )
        return item, override_matches

    if view == "action":
        # Очередь роли: дольше всех ждущие — сверху. Сортировка и страница в SQL
        # (раньше — Python по всем заявкам очереди: ~1.3 с на 260 заявок).
        total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
        page_stmt = (
            stmt.order_by(None).order_by(_queue_waiting_expr(employee.role).asc(), Application.id)
            .offset(params.offset)
            .limit(params.page_size)
        )
        applications = (await session.execute(page_stmt)).scalars().all()
        items = [(await _to_item(application))[0] for application in applications]
    elif discrepancies_only:
        candidates = (await session.execute(stmt)).scalars().all()
        matched: list[ApplicationListItem] = []
        for application in candidates:
            item, override_matches = await _to_item(application)
            if override_matches is False:
                matched.append(item)
        total = len(matched)
        items = matched[params.offset : params.offset + params.page_size]
    else:
        total = len((await session.execute(stmt)).scalars().all())
        page_stmt = stmt.offset(params.offset).limit(params.page_size)
        applications = (await session.execute(page_stmt)).scalars().all()
        items = [(await _to_item(application))[0] for application in applications]

    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


class DecisionOut(BaseModel):
    id: UUID
    stage: str
    outcome: str
    reason_code: str | None
    freeze: bool
    triggered_rules: list[dict[str, Any]]
    # Человеческим языком, с фактическими значениями и порогом — ТЗ, «Карточка
    # заявки»: «ПДН 58% при пороге 55%», не служебный код. Один на правило —
    # переиспользует те же тексты reason_notes, что и комментарий оператору
    # (T11, app.integrations.status_mapping).
    triggered_rules_text: list[str]
    # Причина целиком, когда сработавших правил в triggered_rules нет, а
    # reason_code есть (например, повторная заморозка по перечню пишет
    # triggered_rules=[] — см. _reject_for_active_freeze): без этого карточка
    # честно не могла бы объяснить отказ. Тот же текст, что в комментарии
    # оператору (build_operator_comment). None для одобрения.
    reason_text: str | None
    score: Decimal | None
    score_source: str | None
    approved_amount: Decimal | None
    approved_term: int | None
    offered_tariff: str | None
    required_down_pay: Decimal | None
    ruleset_version_id: UUID
    created_at: datetime
    # T17, шаг 3 (ваше явное указание): пороги в карточке — ТОЛЬКО из версии
    # правил, по которой принято ИМЕННО ЭТО решение (ruleset_version_id выше),
    # не из текущей активной версии — иначе карточка старого решения «врёт»
    # после правки порогов. auto_approve_above — с учётом матрицы (сумма
    # сделки), тем же способом, что считает движок на момент решения
    # (app.engine.rules.resolve_matrix_overrides).
    score_auto_approve_above: float | None
    score_auto_reject_below: float | None
    pti_max: float | None
    # Фактический ПДН решения (features.pti из снимка признаков стадии Б,
    # тот же порог pti_max выше из версии правил этого решения) — для
    # сравнения «ПДН против порога» в карточке.
    pti: float | None
    # T20: почему ПДН нет — чтобы карточка писала «не вычислимо», а не 0% и
    # не молчала. Только для стадии Б (на стадии А ПДН не считается вовсе):
    # "computed" — посчитан (в т.ч. честный 0%, если действующих кредитов в КИ
    # нет); "no_income" — дохода в анкете нет/нулевой (declared_pti=None в
    # снимке, см. compute_declared_pti_features); "no_credit_history" — КИ не
    # получена (решение до/без БКИ), а ПДН по ТЗ — по фактическим обязательствам.
    # T21: "no_installment" — неизвестен платёж по нашей рассрочке (заказчик
    # не прислал deal.monthly_payment и у тарифа нет markup_pct в правилах).
    pti_status: Literal["computed", "no_income", "no_installment", "no_credit_history"] | None
    # Число действующих кредитов по КИ (features.bki_active_contracts_count) —
    # объясняет ПДН 0%: «действующих кредитов нет».
    bki_active_contracts_count: int | None
    # T21: платёж по нашей рассрочке, вошедший в ПДН, и откуда он: "partner"
    # (deal.monthly_payment из /review) или "tariff" (расчёт по наценке из
    # правил — карточка помечает «рассчитан по тарифу»). None — старое решение
    # (ПДН без рассрочки) или платёж неизвестен.
    installment_payment: Decimal | None
    installment_payment_source: Literal["partner", "tariff"] | None


class CheckOut(BaseModel):
    id: UUID
    subject_type: str
    subject_id: UUID | None
    source: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    error_text: str | None
    # Сырой ответ и разбор — только risk_manager/compliance (как фото и
    # кредитный отчёт): могут содержать ПД (паспортные поля МВД, ФИО из
    # перечня и т.п.), admin/viewer получают null (см. has_full_pii ниже).
    parsed: dict[str, Any] | None
    response_raw: dict[str, Any] | None
    can_retry: bool


class StatusHistoryOut(BaseModel):
    from_status: str | None
    to_status: str
    actor_type: str
    employee_id: UUID | None
    created_at: datetime


class DocumentOut(BaseModel):
    id: UUID
    kind: str
    mime: str
    size_bytes: int
    uploaded_at: datetime


class ManualOverrideOut(BaseModel):
    id: UUID
    # Этап решения, которое переопределили: «передано оператору» на стадии А
    # и «серая зона» на стадии Б — разные подписи, поэтому этап нужен фронту.
    decision_stage: str | None
    from_outcome: str
    to_outcome: str
    reason_code: str
    comment: str | None
    created_at: datetime


class OcrResultOut(BaseModel):
    provider: str
    fields: dict[str, Any]
    min_confidence: Decimal | None
    created_at: datetime


class ExternalSnapshotOut(BaseModel):
    id: UUID
    stage: str
    payload_raw: dict[str, Any]
    received_at: datetime


class ApplicationDetail(BaseModel):
    id: UUID
    number: int
    external_id: str
    status: str
    client_name: str | None
    goods_name: str
    goods_price: Decimal
    down_payment: Decimal
    term_months: int
    tariff: str
    requested_amount: Decimal
    submitted_at: datetime
    decisions: list[DecisionOut]
    checks: list[CheckOut]
    status_history: list[StatusHistoryOut]
    documents: list[DocumentOut]
    manual_overrides: list[ManualOverrideOut]
    has_guarantor: bool
    guarantor_id: UUID | None
    # Категория товара — человекочитаемое название из справочника (код из
    # анкеты заказчика в карточку не выводим, см. правило «без сырых кодов»).
    goods_category: str | None
    # T16, п.3 ваших правок: ФССП пришёл ПОСЛЕ уже принятого решения и сработал
    # бы на hard_stop — список субъектов (applicant/guarantor), по которым это
    # случилось, для заметного предупреждения в карточке (fssp_post_decision_alerts).
    fssp_post_decision_alert_subjects: list[str]
    # Анкета/распознавание/снимки — ПД, только risk_manager/compliance (как выше).
    # profile — неизменяемый снимок на момент подачи (см. Application.profile).
    # external_snapshots — ВСЕ снимки анкеты по стадиям (T04/T10:
    # app/integrations/adapter.py пишет новую строку в external_snapshots при
    # КАЖДОМ изменении содержимого — идемпотентно по content_hash), в порядке
    # получения; подсветку полей, изменённых оператором между запусками стадии
    # Б, строит фронт, сравнивая соседние снимки с stage="B".
    profile: dict[str, Any] | None
    ocr_result: OcrResultOut | None
    external_snapshots: list[ExternalSnapshotOut]
    # T16 (ваша правка после отчёта): operator видит ПД ТОЛЬКО в этой карточке —
    # без ФИО/даты рождения/адреса регистрации заявителя и поручителя нельзя ни
    # выбрать регион ФССП, ни отличить должника из найденного производства от
    # однофамильца. Список заявок, клиенты, сотрудники/аудит/правила/импорт —
    # по-прежнему закрыты (_DETAIL_PII_ROLES используется только здесь).
    guarantor_profile: dict[str, Any] | None


@router.get("/{application_id}", response_model=ApplicationDetail)
async def get_application(
    application_id: UUID,
    request: Request,
    employee: Employee = Depends(require_roles(*_VIEW_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> ApplicationDetail:
    application = await session.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="заявка не найдена")

    full_pii = employee.role in _DETAIL_PII_ROLES
    client_name = None
    if application.client_id is not None:
        if full_pii:
            client = await session.get(Client, application.client_id)
            if client is not None:
                client_name = f"{client.last_name} {client.first_name} {client.middle_name or ''}".strip()
        else:
            client_name = _MASKED_NAME

    decisions = (
        await session.execute(
            select(Decision).where(Decision.application_id == application_id).order_by(Decision.created_at)
        )
    ).scalars().all()
    checks = (
        await session.execute(
            select(Check).where(Check.application_id == application_id).order_by(Check.started_at)
        )
    ).scalars().all()
    history = (
        await session.execute(
            select(StatusHistory)
            .where(StatusHistory.application_id == application_id)
            .order_by(StatusHistory.created_at)
        )
    ).scalars().all()
    documents = (
        await session.execute(
            select(Document).where(Document.application_id == application_id).order_by(Document.uploaded_at)
        )
    ).scalars().all()
    overrides = (
        await session.execute(
            select(ManualOverride)
            .where(ManualOverride.application_id == application_id)
            .order_by(ManualOverride.created_at)
        )
    ).scalars().all()
    guarantor_row = (
        await session.execute(select(Guarantor).where(Guarantor.application_id == application_id).limit(1))
    ).scalars().first()
    guarantor_id = guarantor_row.id if guarantor_row is not None else None
    guarantor_profile = guarantor_row.profile if (full_pii and guarantor_row is not None) else None
    fssp_alert_subjects = sorted(
        (
            await session.execute(
                select(FsspPostDecisionAlert.subject_type)
                .where(FsspPostDecisionAlert.application_id == application_id)
                .distinct()
            )
        ).scalars().all()
    )

    goods_category = None
    if application.goods_category_id is not None:
        category_row = await session.get(GoodsCategory, application.goods_category_id)
        if category_row is not None:
            goods_category = category_row.name

    stage_by_decision_id = {d.id: d.stage for d in decisions}

    notes = get_all_reason_notes()
    ocr_result = None
    profile = None
    external_snapshots: list[ExternalSnapshotOut] = []
    if full_pii:
        profile = application.profile
        ocr_row = (
            await session.execute(
                select(OcrResult)
                .where(OcrResult.application_id == application_id)
                .order_by(OcrResult.created_at.desc())
                .limit(1)
            )
        ).scalars().first()
        if ocr_row is not None:
            # fields=ocr_row.fields — ЦЕЛИКОМ, с обёрткой {"fields":...,
            # "flags":...}, намеренно: фронту нужны и сами поля, и флаги
            # (unreadable/cropped/name_mismatch), не только значения. Это не
            # место для unwrap_ocr_fields — та распаковка для кода, которому
            # нужен ПЛОСКИЙ словарь полей (Subject и т.п.), не для ответа API.
            ocr_result = OcrResultOut(
                provider=ocr_row.provider, fields=ocr_row.fields,
                min_confidence=ocr_row.min_confidence, created_at=ocr_row.created_at,
            )
        snapshot_rows = (
            await session.execute(
                select(ExternalSnapshot)
                .where(ExternalSnapshot.application_id == application_id)
                .order_by(ExternalSnapshot.received_at)
            )
        ).scalars().all()
        external_snapshots = [
            ExternalSnapshotOut(id=s.id, stage=s.stage, payload_raw=s.payload_raw, received_at=s.received_at)
            for s in snapshot_rows
        ]

    if full_pii:
        await record_audit(
            session, employee_id=employee.id, action="view_application",
            object_type="application", object_id=application_id, request=request,
        )
        await session.commit()

    # T17, шаг 3: пороги для КАЖДОГО решения — из ЕГО СОБСТВЕННОЙ версии
    # правил (ruleset_version_id), не из текущей активной — иначе карточка
    # старого решения показывала бы порог, который на момент решения мог быть
    # другим. Кэш на запрос — несколько решений часто ссылаются на одну и ту
    # же версию (та же стадия, без правки правил между попытками).
    thresholds_cache: dict[UUID, tuple[float | None, float | None, float | None]] = {}
    decisions_out: list[DecisionOut] = []
    for d in decisions:
        thresholds = thresholds_cache.get(d.ruleset_version_id)
        if thresholds is None:
            ruleset_row = await session.get(RulesetVersion, d.ruleset_version_id)
            thresholds = (None, None, None)
            if ruleset_row is not None:
                try:
                    decision_config = load_config(ruleset_row)
                except ValidationError:
                    # Битая историческая строка не должна ронять всю карточку: это
                    # только отображение порогов, решение от него не зависит.
                    decision_config = None
                if decision_config is not None:
                    resolved, _missing = resolve_matrix_overrides(
                        decision_config, {"amount": float(application.requested_amount)}
                    )
                    thresholds = (
                        resolved.get("auto_approve_above"), resolved.get("auto_reject_below"),
                        decision_config.limits.pti_max,
                    )
            thresholds_cache[d.ruleset_version_id] = thresholds
        approve_above, reject_below, pti_max = thresholds
        pti = None
        pti_status = None
        active_contracts = None
        installment_payment = None
        installment_source = None
        if d.feature_snapshot_id is not None:
            snapshot_row = await session.get(FeatureSnapshot, d.feature_snapshot_id)
            if snapshot_row is not None:
                snapshot_features = snapshot_row.features or {}
                bki_features = snapshot_features.get("features") or {}
                pti = bki_features.get("pti")
                active_contracts = bki_features.get("bki_active_contracts_count")
                raw_installment = snapshot_features.get("installment_monthly_payment")
                if raw_installment is not None:
                    installment_payment = Decimal(str(raw_installment)).quantize(Decimal("0.01"))
                    installment_source = snapshot_features.get("installment_payment_source")
                if d.stage == "B":
                    if pti is not None:
                        pti_status = "computed"
                    elif snapshot_features.get("pti_unavailable_reason") in ("no_income", "no_installment"):
                        pti_status = snapshot_features["pti_unavailable_reason"]
                    elif "declared_pti" in snapshot_features and snapshot_features["declared_pti"] is None:
                        # Снимки до T21 — без явной причины: declared_pti=None
                        # тогда означал только отсутствие дохода.
                        pti_status = "no_income"
                    else:
                        pti_status = "no_credit_history"
        reason_text = None
        if d.outcome != "approve" and (d.triggered_rules or d.reason_code):
            reason_text = build_operator_comment(reason_code=d.reason_code, triggered_rules=d.triggered_rules)
        decisions_out.append(
            DecisionOut(
                id=d.id, stage=d.stage, outcome=d.outcome, reason_code=d.reason_code, freeze=d.freeze,
                triggered_rules=d.triggered_rules,
                triggered_rules_text=[_render_rule_text(r, notes) for r in d.triggered_rules],
                reason_text=reason_text,
                score=d.score, score_source=d.score_source,
                approved_amount=d.approved_amount, approved_term=d.approved_term,
                offered_tariff=d.offered_tariff, required_down_pay=d.required_down_pay,
                ruleset_version_id=d.ruleset_version_id, created_at=d.created_at,
                score_auto_approve_above=approve_above, score_auto_reject_below=reject_below, pti_max=pti_max,
                pti=pti, pti_status=pti_status, bki_active_contracts_count=active_contracts,
                installment_payment=installment_payment, installment_payment_source=installment_source,
            )
        )

    return ApplicationDetail(
        id=application.id, number=application.number, external_id=application.external_id,
        status=application.status, client_name=client_name, goods_name=application.goods_name,
        goods_price=application.goods_price, down_payment=application.down_payment,
        term_months=application.term_months, tariff=application.tariff,
        requested_amount=application.requested_amount, submitted_at=application.submitted_at,
        decisions=decisions_out,
        checks=[
            CheckOut(
                id=c.id, subject_type=c.subject_type, subject_id=c.subject_id, source=c.source, status=c.status,
                started_at=c.started_at, finished_at=c.finished_at, duration_ms=c.duration_ms,
                error_text=c.error_text,
                parsed=c.parsed if full_pii else None,
                response_raw=c.response_raw if full_pii else None,
                can_retry=(c.source in _RETRYABLE_SOURCES and c.status == "source_unavailable"),
            )
            for c in checks
        ],
        status_history=[
            StatusHistoryOut(
                from_status=h.from_status, to_status=h.to_status, actor_type=h.actor_type,
                employee_id=h.employee_id, created_at=h.created_at,
            )
            for h in history
        ],
        documents=[
            DocumentOut(id=d.id, kind=d.kind, mime=d.mime, size_bytes=d.size_bytes, uploaded_at=d.uploaded_at)
            for d in documents
        ],
        manual_overrides=[
            ManualOverrideOut(
                id=o.id, decision_stage=stage_by_decision_id.get(o.decision_id),
                from_outcome=o.from_outcome, to_outcome=o.to_outcome, reason_code=o.reason_code,
                comment=o.comment, created_at=o.created_at,
            )
            for o in overrides
        ],
        has_guarantor=guarantor_id is not None,
        guarantor_id=guarantor_id,
        goods_category=goods_category,
        guarantor_profile=guarantor_profile,
        fssp_post_decision_alert_subjects=fssp_alert_subjects,
        profile=profile,
        ocr_result=ocr_result,
        external_snapshots=external_snapshots,
    )


class CreditReportOut(BaseModel):
    status: str
    report_raw: dict[str, Any] | None
    features: dict[str, Any] | None
    score: Decimal | None
    requested_at: datetime


@router.get("/{application_id}/credit-report", response_model=CreditReportOut)
async def get_credit_report(
    application_id: UUID,
    request: Request,
    employee: Employee = Depends(require_roles(*_CREDIT_REPORT_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> CreditReportOut:
    application = await session.get(Application, application_id)
    if application is None or application.client_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="кредитный отчёт недоступен")

    report = (
        await session.execute(
            select(BkiReport)
            .where(BkiReport.client_id == application.client_id, BkiReport.status == "ok")
            .order_by(BkiReport.requested_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="кредитный отчёт недоступен")

    await record_audit(
        session, employee_id=employee.id, action="view_credit_report",
        object_type="application", object_id=application_id, request=request,
    )
    await session.commit()

    return CreditReportOut(
        status=report.status, report_raw=report.report_raw, features=report.features,
        score=report.score, requested_at=report.requested_at,
    )


@router.get("/{application_id}/documents/{document_id}")
async def view_document(
    application_id: UUID,
    document_id: UUID,
    request: Request,
    response: Response,
    employee: Employee = Depends(require_roles(*_PHOTO_ROLES)),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> Response:
    """Фото — через сессию, не подписанной ссылкой (ваше уточнение): ссылка с
    ключом в адресе остаётся в истории браузера и в логах. Каждый просмотр —
    в audit_log; Cache-Control: no-store — ни браузер, ни промежуточный прокси
    не должны сохранять снимок паспорта на диске."""
    document = await session.get(Document, document_id)
    if document is None or document.application_id != application_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="документ не найден")

    data = await storage.read(document.storage_key)

    await record_audit(
        session, employee_id=employee.id, action="view_document",
        object_type="document", object_id=document_id, request=request,
    )
    await session.commit()

    return Response(content=data, media_type=document.mime, headers={"Cache-Control": "no-store"})


class RetryCheckResult(BaseModel):
    check: CheckOut


@router.post("/{application_id}/checks/{check_id}/retry", response_model=RetryCheckResult)
async def retry_check(
    application_id: UUID,
    check_id: UUID,
    request: Request,
    employee: Employee = Depends(require_roles(*_RETRY_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> RetryCheckResult:
    """Повтор проверки для недоступного источника (ТЗ, «Состояния карточки»:
    «Источник недоступен — плашка с кнопкой повтора»). Только risk_manager;
    только ФНС/Федресурс (_RETRYABLE_SOURCES) и только пока последний результат
    по этому источнику — source_unavailable (иначе незачем повторно дёргать
    рабочий источник или платный БКИ)."""
    application = await session.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="заявка не найдена")

    check = await session.get(Check, check_id)
    if check is None or check.application_id != application_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="проверка не найдена")
    if check.source not in _RETRYABLE_SOURCES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="повтор недоступен для этого источника")
    if check.status != "source_unavailable":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="повтор возможен только для недоступного источника"
        )
    # Check — append-only (правило 1 CLAUDE.md): у ИСХОДНОЙ строки status
    # никогда не поменяется, даже после успешного повтора (он пишет НОВУЮ
    # строку). Поэтому "уже повторяли" проверяем не по check.status, а по
    # тому, остаётся ли эта строка САМОЙ СВЕЖЕЙ для источника — иначе один и
    # тот же устаревший check_id можно было бы "повторять" бесконечно.
    latest_id_for_source = (
        await session.execute(
            select(Check.id)
            .where(
                Check.application_id == application_id, Check.subject_type == check.subject_type,
                Check.subject_id == check.subject_id, Check.source == check.source,
            )
            .order_by(Check.started_at.desc())
            .limit(1)
        )
    ).scalar_one()
    if latest_id_for_source != check_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="по этому источнику уже есть более новый результат — повторите по нему",
        )

    if check.subject_type == "applicant":
        ocr_row = (
            await session.execute(
                select(OcrResult)
                .where(OcrResult.application_id == application_id)
                .order_by(OcrResult.created_at.desc())
                .limit(1)
            )
        ).scalars().first()
        if ocr_row is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="нет данных распознавания для повтора")
        subject = _subject_from_ocr(unwrap_ocr_fields(ocr_row.fields), application)
    else:
        guarantor = await session.get(Guarantor, check.subject_id) if check.subject_id else None
        if guarantor is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="поручитель не найден")
        payload = guarantor.profile
        subject = Subject(
            last_name=payload.get("last_name"), first_name=payload.get("first_name"),
            middle_name=payload.get("middle_name"),
            birth_date=date.fromisoformat(payload["birth_date"]) if payload.get("birth_date") else None,
            passport_series=payload.get("passport_series"), passport_number=payload.get("passport_number"),
            phone=payload.get("phone"),
        )

    settings = get_settings()
    if check.source == "fns_inn":
        new_check = await run_and_record_check(
            session, build_fns_inn_source(settings), application_id=application_id,
            subject_type=check.subject_type, subject_id=check.subject_id, subject=subject,
        )
    else:
        # fns_npd/fns_egrip принимают ИНН, не Subject — берём его из самого
        # свежего fns_inn-результата по этому же subject (ваше указание: без
        # ИНН спрашивать нечего, тот же принцип, что в app/tasks/fns_checks.py).
        inn_check = (
            await session.execute(
                select(Check)
                .where(
                    Check.application_id == application_id, Check.subject_type == check.subject_type,
                    Check.subject_id == check.subject_id, Check.source == "fns_inn", Check.status == "ok",
                )
                .order_by(Check.started_at.desc())
                .limit(1)
            )
        ).scalars().first()
        if inn_check is None or not (inn_check.parsed or {}).get("inn"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="нет найденного ИНН для повтора")
        inn = inn_check.parsed["inn"]

        started_at = datetime.now(timezone.utc)
        if check.source == "fns_npd":
            result = await build_fns_npd_source(settings).check_by_inn(inn, as_of=date.today())
        else:
            result = await build_fns_egrip_source(settings).check_by_inn(inn)
        new_check = await record_check(
            session, application_id=application_id, subject_type=check.subject_type,
            subject_id=check.subject_id, source_name=check.source, result=result,
            started_at=started_at, finished_at=datetime.now(timezone.utc), duration_ms=0,
        )

    await record_audit(
        session, employee_id=employee.id, action="retry_check",
        object_type="check", object_id=check_id, request=request,
        details={"source": check.source, "new_check_id": str(new_check.id)},
    )
    await session.commit()

    return RetryCheckResult(
        check=CheckOut(
            id=new_check.id, subject_type=new_check.subject_type, subject_id=new_check.subject_id,
            source=new_check.source, status=new_check.status, started_at=new_check.started_at,
            finished_at=new_check.finished_at, duration_ms=new_check.duration_ms,
            error_text=new_check.error_text, parsed=new_check.parsed, response_raw=new_check.response_raw,
            can_retry=(new_check.source in _RETRYABLE_SOURCES and new_check.status == "source_unavailable"),
        )
    )
