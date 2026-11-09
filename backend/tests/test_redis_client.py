"""Клиент Redis — свой на каждый цикл событий: в Celery каждая задача —
отдельный asyncio.run(), клиент прошлой задачи падал с «Event loop is closed»
(ИНН и Спектрум со второй задачи воркера)."""

import asyncio

from app.redis import get_redis


def test_new_client_per_event_loop_and_reused_within_loop():
    async def task() -> tuple[object, object]:
        first, second = get_redis(), get_redis()
        await first.ping()  # клиент рабочий в своём цикле
        return first, second

    a1, a2 = asyncio.run(task())
    b1, _ = asyncio.run(task())  # вторая «задача воркера» — новый цикл

    assert a1 is a2  # в одном цикле — тот же клиент
    assert b1 is not a1  # в новом цикле — новый, и он работает (ping выше)
