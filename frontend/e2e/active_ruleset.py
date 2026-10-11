"""T22: активная версия правил — только чтение. global-setup.ts запоминает её
до прогона Playwright, global-teardown.ts сверяет после: прогон не должен
менять активную версию правил в dev-базе (тесты публикации/отката правил
идут на отдельной базе, см. playwright.config.ts). Подаётся в контейнер через
stdin, как seed.py. Печатает одну строку JSON."""

import asyncio
import json

from app.db import new_sessionmaker
from app.engine.versions import get_active_ruleset_version


async def main() -> None:
    engine, session_factory = new_sessionmaker()
    try:
        async with session_factory() as session:
            row = await get_active_ruleset_version(session)
            print(json.dumps({"id": str(row.id), "version": row.version} if row else None))
    finally:
        await engine.dispose()


asyncio.run(main())
