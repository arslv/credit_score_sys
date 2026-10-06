"""Общий минимальный интервал между запросами к внешнему сайту — T16 (ФНС:
несколько доменов одной платформы nalog.ru показывают капчу при превышении
частоты; ФССП: защита от собственного флуда с нашей стороны). Состояние — в
Redis (SET NX PX), не в памяти процесса: верно независимо от того, сколько
воркеров Celery сейчас запущено и с какой конкурентностью — все они бьются за
один и тот же ключ."""

import asyncio

from redis.asyncio import Redis


async def throttle(redis: Redis, key: str, interval_seconds: float) -> None:
    """Возвращается не раньше, чем через interval_seconds после последнего
    успешного вызова throttle() с тем же key (любым процессом). Резервирует
    СЛЕДУЮЩИЙ интервал сразу, как только получает слот — не после фактического
    HTTP-запроса — чтобы время самого запроса не "сокращало" паузу перед
    следующим вызовом."""
    while True:
        reserved = await redis.set(key, "1", nx=True, px=int(interval_seconds * 1000))
        if reserved:
            return
        ttl_ms = await redis.pttl(key)
        wait_seconds = (ttl_ms / 1000) if ttl_ms and ttl_ms > 0 else interval_seconds
        await asyncio.sleep(min(wait_seconds, interval_seconds))
