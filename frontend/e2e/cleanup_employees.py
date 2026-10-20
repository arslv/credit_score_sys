"""T23: после прогона Playwright — удалить (deleted_at, как кнопка «Удалить»
в админке) сотрудников, которых завёл сам прогон: e2e-*@example.com из
seed.py и тестов. Иначе каждый прогон добавлял в dev-базу новых сотрудников,
и список в «Настройки → Сотрудники» разрастался. Подаётся в контейнер через
stdin, как seed.py. Печатает одну строку JSON — сколько удалено."""

import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy import update

from app.db import new_sessionmaker
from app.models import Employee


async def main() -> None:
    engine, session_factory = new_sessionmaker()
    try:
        async with session_factory() as session:
            result = await session.execute(
                update(Employee)
                .where(Employee.email.like("e2e-%@example.com"), Employee.deleted_at.is_(None))
                .values(deleted_at=datetime.now(timezone.utc), active=False)
            )
            await session.commit()
            print(json.dumps({"deleted": result.rowcount}))
    finally:
        await engine.dispose()


asyncio.run(main())
