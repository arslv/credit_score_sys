"""T15: пересчёт delinquency_daily из сырых платежей — DB-слой поверх чистого
app/engine/delinquency.py (по той же причине, что app/engine/versions.py
вынесен из app/engine/rules.py: чистая функция отдельно от работы с БД).

Пересчитывает ВСЮ историю договора (issued_at .. upto_date) при каждом
вызове, не только "сегодня" инкрементально. Это сознательное упрощение (ваше
явное указание, CLAUDE.md правило 1 с явным исключением для этой таблицы):
delinquency_daily — целиком вычислимый кэш, перезапись разрешена, поэтому
ежедневная задача (app/tasks/delinquency.py) и команда полного пересчёта
(app/cli/recompute_delinquency.py) — буквально один и тот же код, и поэтому
тест «полный пересчёт = накопленные ежедневные задачи» верен по построению,
а не случайно. При текущем масштабе (ТЗ: «несколько десятков заявок в
месяц») это не проблема производительности; оптимизация "пересчитывать только
с даты последнего изменения" — осознанно не делалась, не нужна на этом
масштабе."""

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.engine.delinquency import ScheduleItem, compute_delinquency_series
from app.models import Contract, DelinquencyDaily, Payment, PaymentSchedule

MOSCOW = ZoneInfo("Europe/Moscow")


def moscow_today() -> date:
    """"Сегодня" по московскому календарю (ваше явное указание) — не UTC:
    полночь по Москве наступает раньше полуночи UTC, иначе платёж/просрочка
    на границе суток считались бы не тем днём."""
    return datetime.now(MOSCOW).date()


def load_grace_days() -> int:
    raw = yaml.safe_load(Path(get_settings().collections_config_path).read_text(encoding="utf-8"))
    return int(raw["grace_days"])


async def recompute_contract(session: AsyncSession, contract: Contract, *, upto_date: date, grace_days: int) -> int:
    """Возвращает число пересчитанных дней."""
    if upto_date < contract.issued_at:
        return 0

    schedule_rows = (
        await session.execute(select(PaymentSchedule).where(PaymentSchedule.contract_id == contract.id))
    ).scalars().all()
    schedule = [
        ScheduleItem(period_no=r.period_no, due_date=r.due_date, due_amount=r.due_amount) for r in schedule_rows
    ]
    if not schedule:
        return 0

    payment_rows = (
        await session.execute(
            select(Payment).where(Payment.contract_id == contract.id, Payment.kind != "penalty")
        )
    ).scalars().all()
    ledger: list[tuple[date, Decimal]] = [
        (p.paid_at, p.amount if p.kind == "regular" else -p.amount) for p in payment_rows
    ]

    series = compute_delinquency_series(schedule, ledger, contract.issued_at, upto_date, grace_days)
    if not series:
        return 0

    now = datetime.now(timezone.utc)
    rows = [
        {
            "contract_id": contract.id, "as_of": as_of,
            "days_past_due": snapshot.days_past_due, "overdue_amount": snapshot.overdue_amount,
            "computed_at": now,
        }
        for as_of, snapshot in series.items()
    ]
    stmt = pg_insert(DelinquencyDaily).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[DelinquencyDaily.contract_id, DelinquencyDaily.as_of],
        set_={
            "days_past_due": stmt.excluded.days_past_due,
            "overdue_amount": stmt.excluded.overdue_amount,
            "computed_at": stmt.excluded.computed_at,
        },
    )
    await session.execute(stmt)
    return len(rows)


async def recompute_all_contracts(session: AsyncSession, *, upto_date: date | None = None) -> dict[str, int]:
    grace_days = load_grace_days()
    upto_date = upto_date or moscow_today()

    contracts = (await session.execute(select(Contract))).scalars().all()
    total_days = 0
    for contract in contracts:
        total_days += await recompute_contract(session, contract, upto_date=upto_date, grace_days=grace_days)
    return {"contracts": len(contracts), "days_written": total_days}
