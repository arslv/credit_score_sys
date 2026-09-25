"""Вспомогательный исполнитель для scripts/e2e_check.py — копируется в контейнер
воркера (docker compose cp) и запускается там (docker compose exec), потому что
именно там лежит код приложения и есть доступ к БД/Redis. Сам e2e_check.py
работает на хосте и оркестрирует через docker compose exec/cp, не импортируя
app.* напрямую — так одним и тем же способом (CLI-вызов) запускаются и
настоящие Celery-задачи, и служебные операции с БД для проверки результата.

Использование: python3 _e2e_runner.py <action>, аргументы — JSON в stdin,
результат — одна строка JSON в stdout.
"""

import asyncio
import json
import logging
import sys
from datetime import date, datetime, timezone

import structlog
from sqlalchemy import select

# По умолчанию (без app.logging.configure_logging(), которую вызывает только
# app/main.py при старте FastAPI — Celery-задачи её не вызывают) structlog
# пишет в stdout своим PrintLogger, ЛИБО через app.logging позже настраивает
# stdout явно. И то, и другое ломает протокол "одна строка JSON на stdout" у
# этого раннера — логи из run_stage_a/run_stage_b (обычный INFO при обработке
# заявки) подмешиваются ПЕРЕД финальным print(). Переносим на stderr ДО любого
# импорта app.tasks.* (structlog.configure можно звать повторно — последний
# вызов побеждает, но лучше сделать это первым, чем гадать, что уже успело
# закэшировать логгер).
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))

from app.db import new_sessionmaker  # noqa: E402
from app.models import (  # noqa: E402
    Application,
    BkiReport,
    Client,
    Decision,
    SanctionsList,
    StatusHistory,
)


async def _run_task(args: dict) -> dict:
    name = args["task"]
    if name == "sync":
        from app.tasks.integration import _run as run
    elif name == "stage_a":
        from app.tasks.stage_a import _run as run
    elif name == "stage_b":
        from app.tasks.stage_b import _run as run
    else:
        raise ValueError(f"unknown task {name!r}")
    return await run()


async def _seed_sanctions(args: dict) -> dict:
    engine, session_factory = new_sessionmaker()
    try:
        async with session_factory() as session:
            session.add(
                SanctionsList(
                    list_type=args.get("list_type", "te"),
                    full_name=args["full_name"],
                    name_normalized=args["name_normalized"],
                    birth_date=date.fromisoformat(args["birth_date"]) if args.get("birth_date") else None,
                    loaded_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
        return {"ok": True}
    finally:
        await engine.dispose()


async def _app_state(args: dict) -> dict:
    engine, session_factory = new_sessionmaker()
    try:
        async with session_factory() as session:
            application = (
                await session.execute(select(Application).where(Application.external_id == args["external_id"]))
            ).scalar_one_or_none()
            if application is None:
                return {"found": False}

            history = (
                await session.execute(
                    select(StatusHistory)
                    .where(StatusHistory.application_id == application.id)
                    .order_by(StatusHistory.created_at)
                )
            ).scalars().all()

            decisions = (
                await session.execute(
                    select(Decision)
                    .where(Decision.application_id == application.id)
                    .order_by(Decision.created_at)
                )
            ).scalars().all()

            return {
                "found": True,
                "status": application.status,
                "client_id": str(application.client_id) if application.client_id else None,
                "path": [
                    {"from": h.from_status, "to": h.to_status, "actor_type": h.actor_type} for h in history
                ],
                "decisions": [
                    {
                        "stage": d.stage,
                        "outcome": d.outcome,
                        "reason_code": d.reason_code,
                        "score": float(d.score) if d.score is not None else None,
                        "approved_amount": float(d.approved_amount) if d.approved_amount is not None else None,
                        "approved_term": d.approved_term,
                        "offered_tariff": d.offered_tariff,
                        "required_down_pay": (
                            float(d.required_down_pay) if d.required_down_pay is not None else None
                        ),
                        "freeze": d.freeze,
                    }
                    for d in decisions
                ],
            }
    finally:
        await engine.dispose()


async def _bki_attempt_count(args: dict) -> dict:
    engine, session_factory = new_sessionmaker()
    try:
        async with session_factory() as session:
            application = (
                await session.execute(select(Application).where(Application.external_id == args["external_id"]))
            ).scalar_one_or_none()
            if application is None or application.client_id is None:
                return {"client_id": None, "count": 0}
            rows = (
                await session.execute(select(BkiReport).where(BkiReport.client_id == application.client_id))
            ).scalars().all()
            return {"client_id": str(application.client_id), "count": len(rows), "statuses": [r.status for r in rows]}
    finally:
        await engine.dispose()


async def _checks_called(args: dict) -> dict:
    """Какие внешние источники реально запускали check() для заявителя этой
    заявки (проверка "внешние источники не вызывались" для сценария 2)."""
    from app.models import Check

    engine, session_factory = new_sessionmaker()
    try:
        async with session_factory() as session:
            application = (
                await session.execute(select(Application).where(Application.external_id == args["external_id"]))
            ).scalar_one_or_none()
            if application is None:
                return {"sources": []}
            rows = (
                await session.execute(
                    select(Check.source).where(
                        Check.application_id == application.id, Check.subject_type == "applicant"
                    )
                )
            ).scalars().all()
            return {"sources": sorted(set(rows))}
    finally:
        await engine.dispose()


_ACTIONS = {
    "run_task": _run_task,
    "seed_sanctions": _seed_sanctions,
    "app_state": _app_state,
    "bki_attempt_count": _bki_attempt_count,
    "checks_called": _checks_called,
}


def main() -> None:
    action = sys.argv[1]
    raw_args = sys.stdin.read()
    args = json.loads(raw_args) if raw_args.strip() else {}
    result = asyncio.run(_ACTIONS[action](args))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
