"""Виртуальные часы демо-сида (T23, ваше решение: «виртуальные часы»).

Конвейер берёт время из datetime.now(timezone.utc), а created_at/
received_at и т.п. заполняет сам Postgres (server_default now()). Внутри
virtual_time(dt):
- time_machine подменяет datetime.now()/time.time() (monotonic не трогает —
  asyncio и asyncpg работают как обычно);
- слушатель before_flush ставит в каждой НОВОЙ строке ещё не заданные
  колонки с server_default now() — тем же виртуальным временем. Только
  INSERT: существующие строки не трогаются (правило 1 CLAUDE.md).

Используется только демо-сидом; в обычной работе слушатель не установлен."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session


def _is_now_default(column) -> bool:
    server_default = column.server_default
    if server_default is None:
        return False
    arg = getattr(server_default, "arg", None)
    return getattr(arg, "name", None) == "now"


def _stamp_new_rows(session: Session, flush_context, instances) -> None:
    now = datetime.now(timezone.utc)
    for obj in session.new:
        mapper = inspect(obj).mapper
        for attr in mapper.column_attrs:
            column = attr.columns[0]
            if _is_now_default(column) and getattr(obj, attr.key) is None:
                setattr(obj, attr.key, now)


_installed = False


def install_timestamp_listener() -> None:
    """Идемпотентно: повторный вызов ничего не добавляет."""
    global _installed
    if not _installed:
        event.listen(Session, "before_flush", _stamp_new_rows)
        _installed = True


def uninstall_timestamp_listener() -> None:
    global _installed
    if _installed:
        event.remove(Session, "before_flush", _stamp_new_rows)
        _installed = False


@contextmanager
def virtual_time(moment: datetime) -> Iterator[None]:
    """Время идёт (tick=True) начиная с moment — между шагами одной заявки
    проходят реальные доли секунды, порядок событий сохраняется."""
    import time_machine  # dev-зависимость: нужна только сиду

    if moment.tzinfo is None:
        raise ValueError("moment must be timezone-aware (правило 6 CLAUDE.md)")
    with time_machine.travel(moment, tick=True):
        yield
