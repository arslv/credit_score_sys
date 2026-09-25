import asyncio
import hashlib
import json
from decimal import Decimal
from typing import Any, Awaitable, Callable

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.integrations.base import (
    PartnerClient,
    PartnerConflictError,
    PartnerUnavailableError,
)
from app.models import (
    Application,
    Decision,
    Document,
    ExternalSnapshot,
    IntegrationEvent,
    ManualOverride,
    StatusHistory,
)
from app.storage.base import Storage

logger = structlog.get_logger()


def _content_hash(raw: dict[str, Any]) -> str:
    canonical = json.dumps(raw, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _log_event(
    session_factory: async_sessionmaker,
    *,
    application_id,
    direction: str,
    operation: str,
    status: str,
    attempt: int,
    response: dict[str, Any] | None = None,
) -> None:
    async with session_factory() as session:
        session.add(
            IntegrationEvent(
                application_id=application_id,
                direction=direction,
                operation=operation,
                status=status,
                attempt=attempt,
                response=response,
            )
        )
        await session.commit()


async def _call_with_retry(
    session_factory: async_sessionmaker,
    *,
    application_id,
    direction: str,
    operation: str,
    func: Callable[[], Awaitable[Any]],
) -> Any:
    """Оборачивает один вызов PartnerClient повторами с нарастающей задержкой.
    Каждая попытка — своя строка в integration_events (правило из ТЗ «Идемпотентность
    и сбои»). PartnerConflictError не перехватывается — это не временный сбой."""
    settings = get_settings()
    delay = settings.integration_retry_base_delay_seconds

    for attempt in range(1, settings.integration_retry_max_attempts + 1):
        try:
            result = await func()
        except PartnerUnavailableError as exc:
            is_last = attempt == settings.integration_retry_max_attempts
            await _log_event(
                session_factory,
                application_id=application_id,
                direction=direction,
                operation=operation,
                status="error" if is_last else "retry",
                attempt=attempt,
                response={"error": str(exc)},
            )
            if is_last:
                raise
            await asyncio.sleep(min(delay, settings.integration_retry_max_delay_seconds))
            delay *= 2
            continue
        else:
            await _log_event(
                session_factory,
                application_id=application_id,
                direction=direction,
                operation=operation,
                status="ok",
                attempt=attempt,
            )
            return result

    raise AssertionError("unreachable")  # pragma: no cover


async def _sync_photo(
    session_factory: async_sessionmaker,
    storage: Storage,
    client: PartnerClient,
    session,
    application_uuid,
    external_id: str,
    kind: str,
) -> None:
    try:
        data = await _call_with_retry(
            session_factory,
            application_id=application_uuid,
            direction="inbound",
            operation=f"download_photo:{kind}",
            func=lambda: client.download_photo(external_id, kind),
        )
    except PartnerUnavailableError:
        logger.warning("partner_photo_download_failed", external_id=external_id, kind=kind)
        return

    sha = _sha256(data)
    existing = (
        await session.execute(
            select(Document).where(
                Document.application_id == application_uuid,
                Document.kind == kind,
                Document.sha256 == sha,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return

    key = f"{application_uuid}/{kind}/{sha}.bin"
    storage_key = await storage.save(key, data)
    session.add(
        Document(
            application_id=application_uuid,
            kind=kind,
            storage_key=storage_key,
            sha256=sha,
            mime="image/png",
            size_bytes=len(data),
        )
    )


async def _sync_one_application(
    session_factory: async_sessionmaker,
    client: PartnerClient,
    storage: Storage,
    external_id: str,
    stage: str,
) -> str:
    detail = await _call_with_retry(
        session_factory,
        application_id=None,  # наш внутренний id ещё не известен
        direction="inbound",
        operation="get_application",
        func=lambda: client.get_application(external_id),
    )

    async with session_factory() as session:
        application = (
            await session.execute(select(Application).where(Application.external_id == external_id))
        ).scalar_one_or_none()

        created = application is None
        if created:
            # Клиент известен только после успешного распознавания паспорта (T05) —
            # до этого клиента у заявки нет, есть только ФИО/телефон из формы.
            goods_price = detail.goods_price if detail.goods_price is not None else Decimal("0")
            down_payment = detail.down_payment if detail.down_payment is not None else Decimal("0")
            application = Application(
                external_id=external_id,
                status="received",
                goods_name=detail.goods_name,
                merchant_name=detail.merchant_name,
                merchant_city=detail.merchant_city,
                goods_price=goods_price,
                down_payment=down_payment,
                term_months=detail.term_months or 0,
                tariff=detail.tariff or "unknown",
                requested_amount=goods_price - down_payment,
                profile=detail.raw.get("form", {}),
                ip=detail.technical_ip,
                user_agent=detail.technical_user_agent,
                consent_pd_at=detail.consent_pd_at,
                consent_bki_at=detail.consent_bki_at,
                submitted_at=detail.created_at,
            )
            session.add(application)
            await session.flush()
            session.add(
                StatusHistory(
                    application_id=application.id,
                    from_status=None,
                    to_status="received",
                    actor_type="system",
                )
            )
            # Коммитим сразу: _sync_photo ниже логирует через отдельную сессию
            # (своя транзакция для integration_events), которой нужна видимая
            # (закоммиченная) строка applications для FK.
            await session.commit()

        content_hash = _content_hash(detail.raw)
        latest_snapshot = (
            await session.execute(
                select(ExternalSnapshot)
                .where(
                    ExternalSnapshot.application_id == application.id,
                    ExternalSnapshot.stage == stage,
                )
                .order_by(ExternalSnapshot.received_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        # Идемпотентность по external_id + хешу снимка: тот же снимок повторно не пишем.
        is_new_snapshot = latest_snapshot is None or latest_snapshot.content_hash != content_hash
        if is_new_snapshot:
            session.add(
                ExternalSnapshot(
                    application_id=application.id,
                    stage=stage,
                    payload_raw=detail.raw,
                    content_hash=content_hash,
                )
            )
            for kind in detail.photo_kinds:
                await _sync_photo(
                    session_factory, storage, client, session, application.id, external_id, kind
                )

        await session.commit()

    if created:
        return "created"
    if is_new_snapshot:
        return "updated"
    return "skipped"


async def sync_applications(
    session_factory: async_sessionmaker, client: PartnerClient, storage: Storage
) -> dict[str, int]:
    """Опрашивает новые заявки и заявки «на рассмотрение», складывает снимки,
    фото и документы. Не запускает распознавание и проверки — это T05+."""
    summary = {"created": 0, "updated": 0, "skipped": 0, "errors": 0}

    for partner_status, stage in (("new", "A"), ("on_review", "B")):
        try:
            items = await _call_with_retry(
                session_factory,
                application_id=None,
                direction="inbound",
                operation=f"list_applications:{partner_status}",
                func=lambda s=partner_status: client.list_applications(s),
            )
        except PartnerUnavailableError:
            logger.warning("partner_list_failed", partner_status=partner_status)
            summary["errors"] += 1
            continue

        for item in items:
            try:
                result = await _sync_one_application(
                    session_factory, client, storage, item.external_id, stage
                )
                summary[result] += 1
            except PartnerUnavailableError:
                summary["errors"] += 1
                logger.warning("partner_sync_failed", external_id=item.external_id)
            except Exception:
                summary["errors"] += 1
                logger.exception("partner_sync_unexpected_error", external_id=item.external_id)

    return summary


async def write_decision_status(
    session_factory: async_sessionmaker,
    client: PartnerClient,
    *,
    application_id,
    external_id: str,
    expected_current_status: str,
    new_status: str,
    comment: str | None = None,
) -> str:
    """Пишет наш исход в систему заказчика. Если статус там уже не совпадает
    с ожидаемым (оператор сменил вручную), запись отменяется — событие conflict
    (правило 12 CLAUDE.md)."""
    try:
        await _call_with_retry(
            session_factory,
            application_id=application_id,
            direction="outbound",
            operation="write_status",
            func=lambda: client.write_status(external_id, expected_current_status, new_status, comment),
        )
    except PartnerConflictError as exc:
        await _log_event(
            session_factory,
            application_id=application_id,
            direction="outbound",
            operation="write_status",
            status="conflict",
            attempt=1,
            response={"expected": exc.expected, "actual": exc.actual},
        )
        await _record_manual_override(
            session_factory, client, application_id=application_id, external_id=external_id,
            our_status=new_status, actual_status=exc.actual,
        )
        return "conflict"
    return "ok"


async def _record_manual_override(
    session_factory: async_sessionmaker, client: PartnerClient, *, application_id, external_id: str,
    our_status: str, actual_status: str,
) -> None:
    """T10: оператор сменил статус вручную до нашей записи (правило 12
    CLAUDE.md, PartnerConflictError выше) — это и есть его решение. Пишем
    расхождение в manual_overrides отдельной строкой, БЕЗ сотрудника-актора с
    нашей стороны (employee_id=NULL — см. app/models/override.py). reason_code —
    один из двух заведённых в T10 (migration 0009); comment — причина оператора
    из системы заказчика, если она там появилась (новое поле, T10)."""
    try:
        detail = await client.get_application(external_id)
    except Exception:
        logger.exception("manual_override_detail_fetch_failed", application_id=str(application_id))
        return

    operator_reason = None
    for entry in reversed(detail.raw.get("status_history", [])):
        if entry.get("changed_by") == "operator":
            operator_reason = entry.get("reason")
            break

    reason_code = (
        "PARTNER_OPERATOR_OVERRIDE_APPROVE" if actual_status == "approved" else "PARTNER_OPERATOR_OVERRIDE_REJECT"
    )

    async with session_factory() as session:
        decision_id = (
            await session.execute(
                select(Decision.id)
                .where(Decision.application_id == application_id)
                .order_by(Decision.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        session.add(
            ManualOverride(
                application_id=application_id,
                decision_id=decision_id,
                employee_id=None,
                from_outcome=our_status,
                to_outcome=actual_status,
                reason_code=reason_code,
                comment=operator_reason,
            )
        )
        await session.commit()

    logger.info(
        "manual_override_recorded", application_id=str(application_id),
        from_outcome=our_status, to_outcome=actual_status,
    )


async def write_comment(
    session_factory: async_sessionmaker,
    client: PartnerClient,
    *,
    application_id,
    external_id: str,
    text: str,
) -> None:
    await _call_with_retry(
        session_factory,
        application_id=application_id,
        direction="outbound",
        operation="add_comment",
        func=lambda: client.add_comment(external_id, text),
    )
