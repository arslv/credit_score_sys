"""Удаление заявки целиком — для тестовых заявок на боевом сервере (решение
владельца 24.11.2026: тестовые «Иванов Иван Иванович» от CRM удалить насовсем).

Осознанное исключение из правила 1 CLAUDE.md («ничего не перезаписываем»):
только консольная команда, не эндпоинт и не кнопка в админке. Удаляет заявку
и всё, что на неё ссылается (проверки, решения, история статусов, OCR, БКИ,
события обмена с CRM…), — по внешним ключам, одной транзакцией. Клиент
(clients) остаётся: он может быть общим с другими заявками. Фото в хранилище
не трогаем.

По умолчанию — только показ: что и сколько строк удалится. Удаляет с --yes.

    docker compose exec api python -m app.cli.delete_application --external-id 424250 --external-id 3
    docker compose exec api python -m app.cli.delete_application --external-id 424250 --external-id 3 --yes
    (заявка, заведённая вручную, — по нашему номеру: --number 7)

Факт удаления — в лог сервиса (номер заявки и номер в CRM, без персданных).
Журнал аудита не годится: список его действий закреплён в схеме БД.
"""

import argparse
import asyncio
import sys
import uuid
from collections.abc import Sequence

import structlog
from sqlalchemy import Table, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

import app.models  # noqa: F401 — все таблицы в Base.metadata
from app.db import new_sessionmaker
from app.models import Application
from app.models.base import Base

logger = structlog.get_logger(__name__)


def _plan(table: Table, where: ColumnElement, path: tuple[str, ...] = ()) -> list[tuple[Table, ColumnElement]]:
    """Порядок удаления: сначала всё, что ссылается на строки table (рекурсивно,
    по внешним ключам), потом сами строки. Таблица может встретиться дважды
    (ссылается и на заявку, и на её решение) — второй проход просто найдёт 0."""
    steps: list[tuple[Table, ColumnElement]] = []
    for child in Base.metadata.sorted_tables:
        if child.name in path or child is table:
            continue
        for fk in child.foreign_keys:
            if fk.column.table is table:
                child_where = fk.parent.in_(select(fk.column).where(where))
                steps.extend(_plan(child, child_where, (*path, table.name)))
    steps.append((table, where))
    return steps


async def find_applications(
    session: AsyncSession, external_ids: Sequence[str], numbers: Sequence[int]
) -> list[Application]:
    conditions = []
    if external_ids:
        conditions.append(Application.external_id.in_(list(external_ids)))
    if numbers:
        conditions.append(Application.number.in_(list(numbers)))
    if not conditions:
        return []
    rows = await session.execute(select(Application).where(or_(*conditions)).order_by(Application.number))
    return list(rows.scalars().all())


async def delete_applications(
    session: AsyncSession, application_ids: Sequence[uuid.UUID], *, dry_run: bool
) -> list[tuple[str, int]]:
    """[(таблица, сколько строк)] — только ненулевые. dry_run — только считает."""
    applications = Application.__table__
    steps = _plan(applications, applications.c.id.in_(list(application_ids)))
    counts: dict[str, int] = {}
    for table, where in steps:
        if dry_run:
            n = (await session.execute(select(func.count()).select_from(table).where(where))).scalar_one()
        else:
            n = (await session.execute(table.delete().where(where))).rowcount or 0
        if n:
            counts[table.name] = counts.get(table.name, 0) + n
    return sorted(counts.items())


async def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Удалить заявку целиком (тестовые заявки).")
    parser.add_argument("--external-id", action="append", default=[], help="номер заявки в CRM (можно несколько)")
    parser.add_argument("--number", action="append", type=int, default=[], help="наш номер заявки (можно несколько)")
    parser.add_argument("--yes", action="store_true", help="удалить по-настоящему (без него — только показ)")
    args = parser.parse_args(argv)
    if not args.external_id and not args.number:
        parser.error("укажите --external-id или --number")

    engine, session_factory = new_sessionmaker()
    try:
        async with session_factory() as session:
            found = await find_applications(session, args.external_id, args.number)
            if not found:
                print("Заявок с такими номерами нет.")
                return 1
            print("Заявки:")
            for a in found:
                print(f"  №{a.number}  в CRM: {a.external_id or '—'}  статус: {a.status}  поступила: {a.submitted_at:%d.%m.%Y %H:%M} UTC")
            missing = set(args.external_id) - {a.external_id for a in found}
            missing_numbers = set(args.number) - {a.number for a in found}
            if missing or missing_numbers:
                print("Не найдены:", ", ".join([*sorted(missing), *map(str, sorted(missing_numbers))]))

            counts = await delete_applications(session, [a.id for a in found], dry_run=not args.yes)
            print("Удалится:" if not args.yes else "Удалено:")
            for table, n in counts:
                print(f"  {table}: {n}")
            if not args.yes:
                print("\nЭто только показ. Чтобы удалить, повторите команду с --yes. Вернуть будет нельзя.")
                return 0
            await session.commit()
            for a in found:
                logger.warning("application_deleted", application_number=a.number, external_id=a.external_id)
            print("Готово.")
            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
