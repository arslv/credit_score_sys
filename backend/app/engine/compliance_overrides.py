"""Ложное совпадение по перечню (T12): комплаенс помечает конкретную пару
(клиент, запись перечня) как false_positive — при следующих проверках ЭТОГО
клиента совпадение с ТОЙ ЖЕ записью не даёт hit/отказ, только информационная
пометка. Сырой Check(status=hit) не переписывается (правило 1 CLAUDE.md,
правило 3 — сырой ответ источника) — подавление происходит здесь, на уровне
признаков для движка правил, при сборке features["checks"] в
app/engine/pipeline.py (заявитель) и app/engine/stage_b_pipeline.py
(поручитель). Ключ пары — sanctions_entry_key (устойчивый признак записи,
переживает перезаливку реестра, см. app/sources/name_normalize), не
sanctions_list.id. Если запись перечня ДЕЙСТВИТЕЛЬНО изменилась (другое ФИО/ДР)
или совпадение — с другой записью, ключ другой и подавление не применяется:
проверка отрабатывает как обычно.

При сбое ПОИСКА пометки (БД недоступна и т.п.) — отказ, не подавление (ваше
уточнение): санкционная проверка должна вести себя как «при сбое — отказ», а
не тихо пропускать совпадение из-за технической ошибки поиска отметки."""

import structlog
from sqlalchemy import select

from app.models import ComplianceReview

logger = structlog.get_logger()


async def _fetch_latest_verdict(session, *, client_id, sanctions_entry_key: str):
    return (
        await session.execute(
            select(ComplianceReview.verdict, ComplianceReview.employee_id, ComplianceReview.created_at)
            .where(
                ComplianceReview.client_id == client_id,
                ComplianceReview.sanctions_entry_key == sanctions_entry_key,
            )
            .order_by(ComplianceReview.created_at.desc())
            .limit(1)
        )
    ).first()


async def suppress_reviewed_false_positive(
    session, *, client_id, check_status: str, check_parsed: dict | None
) -> tuple[str, dict | None]:
    """Возвращает (status, parsed), которые нужно положить в features["checks"]
    вместо сырых — не трогает саму строку Check."""
    if client_id is None or check_status != "hit" or not check_parsed:
        return check_status, check_parsed

    sanctions_entry_key = check_parsed.get("sanctions_entry_key")
    if not sanctions_entry_key:
        return check_status, check_parsed

    try:
        row = await _fetch_latest_verdict(session, client_id=client_id, sanctions_entry_key=sanctions_entry_key)
    except Exception:
        # При сбое — отказ: совпадение НЕ подавляется, санкционная проверка
        # отрабатывает как обычный hit (движок правил отклонит и заморозит).
        logger.error(
            "compliance_review_lookup_failed", client_id=str(client_id), sanctions_entry_key=sanctions_entry_key
        )
        return check_status, check_parsed

    if row is None or row[0] != "false_positive":
        return check_status, check_parsed

    verdict, employee_id, reviewed_at = row
    return "ok", {
        **check_parsed,
        "false_positive_reviewed": True,
        "false_positive_reviewed_at": reviewed_at.isoformat(),
        "false_positive_reviewed_by": str(employee_id),
    }
