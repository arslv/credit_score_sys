"""Полный пересчёт delinquency_daily с нуля для всех договоров (T15, ваше явное
указание) — та же функция, что использует ежедневная задача
(app/tasks/delinquency.py), просто вызванная вручную по требованию, не по
расписанию. Разрешено CLAUDE.md правилом 1 (явное исключение для этой
таблицы — производные данные, перезапись не нарушает append-only для
payments).

Запуск: docker compose exec api python -m app.cli.recompute_delinquency
"""

import asyncio
import sys

from app.db import new_sessionmaker
from app.engine.delinquency_recompute import recompute_all_contracts


async def _run() -> int:
    engine, session_factory = new_sessionmaker()
    try:
        async with session_factory() as session:
            summary = await recompute_all_contracts(session)
            await session.commit()
        print(f"Пересчитано договоров: {summary['contracts']}, дней записано: {summary['days_written']}")
        return 0
    finally:
        await engine.dispose()


def main() -> None:
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
