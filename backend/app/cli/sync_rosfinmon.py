"""Проверка и разовая загрузка перечня Т/Э с открытой страницы Росфинмониторинга.

    docker compose exec worker python -m app.cli.sync_rosfinmon --dry-run
        скачать и разобрать страницу, показать только количество, в базу не писать

    docker compose exec worker python -m app.cli.sync_rosfinmon
        то же и заменить перечень в базе (как ежедневная задача)

Работает независимо от ROSFINMON_SOURCE — чтобы проверить, прежде чем
включать ежедневную загрузку. ФИО не печатаются (CLAUDE.md, правило 7)."""

import argparse
import asyncio
import sys

from app.config import get_settings
from app.db import new_sessionmaker
from app.registries.fedsfm import persons_to_sanctions_csv
from app.registries.jobs import sync_sanctions_list
from app.tasks.registries import build_fedsfm_downloader


async def _main(dry_run: bool) -> int:
    downloader = build_fedsfm_downloader(get_settings())
    try:
        persons = await downloader.fetch_persons()
    except Exception as exc:
        print(f"Не удалось: {exc}")
        return 1
    with_aliases = len(persons)
    print(f"Страница разобрана: строк перечня (с прежними именами) — {with_aliases}.")
    if dry_run:
        print("Пробный запуск: в базу ничего не записано.")
        return 0

    class _Fetched:
        # Страница уже скачана — второй раз сайт не дёргаем.
        async def download(self, registry: str) -> bytes:
            return persons_to_sanctions_csv(persons)

    engine, session_factory = new_sessionmaker()
    try:
        log = await sync_sanctions_list(session_factory, _Fetched())
    finally:
        await engine.dispose()
    print(f"Перечень в базе заменён: {log.status}, строк — {log.rows_loaded}.")
    return 0 if log.status == "ok" else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Перечень Т/Э с открытой страницы Росфинмониторинга")
    parser.add_argument("--dry-run", action="store_true", help="только скачать и разобрать, в базу не писать")
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args.dry_run)))


if __name__ == "__main__":
    main()
