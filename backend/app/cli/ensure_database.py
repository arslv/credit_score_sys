"""T22: создать базу из DATABASE_URL, если её ещё нет, — для отдельной
e2e-базы dialog_e2e (docker compose --profile e2e, сервис api_e2e). Только
CREATE, никогда не DROP и не очистка: существующая база не трогается.

Запуск: python -m app.cli.ensure_database
"""

import asyncio
from urllib.parse import urlsplit

import asyncpg

from app.config import get_settings


async def _run() -> None:
    parts = urlsplit(get_settings().database_url.replace("+asyncpg", ""))
    db_name = parts.path.lstrip("/")
    conn = await asyncpg.connect(
        host=parts.hostname, port=parts.port or 5432, user=parts.username, password=parts.password,
        database="postgres",
    )
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{db_name}"')
            print(f"база {db_name} создана")
        else:
            print(f"база {db_name} уже есть")
    finally:
        await conn.close()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
