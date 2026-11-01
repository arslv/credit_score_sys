"""Временная блокировка поиска на сайте ФССП.

После нескольких неверных кодов (или частых запросов) сайт вместо капчи
отвечает «Извините, что-то пошло не так. Попробуйте позже» — сколько длится
такая блокировка, сайт не сообщает. Поэтому:

- после такого ответа сайт не дёргаем FSSP_BLOCK_COOLDOWN_SECONDS (каждый
  повтор может продлевать блокировку) — сотрудник видит, когда можно снова;
- когда поиск снова прошёл, запоминаем, сколько блокировка длилась на самом
  деле (последние 5 раз), и показываем это в следующий раз как ориентир.

Состояние — в Redis (общее для api и worker), не бизнес-факт: каждая попытка
и так пишется в checks/audit_log."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from redis.asyncio import Redis

_SINCE = "fssp:block:since"
_LAST = "fssp:block:last"
_DURATIONS = "fssp:block:durations"
_KEEP = 5
_TTL_SECONDS = 24 * 3600


@dataclass(frozen=True)
class FsspBlock:
    since: datetime
    retry_at: datetime
    typical_minutes: int | None  # сколько длилась блокировка в прошлые разы (медиана)

    def to_detail(self, message: str) -> dict:
        return {
            "code": "fssp_rate_limited",
            "message": message,
            "since": self.since.isoformat(),
            "retry_at": self.retry_at.isoformat(),
            "typical_minutes": self.typical_minutes,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value) -> datetime | None:
    if value is None:
        return None
    text = value.decode() if isinstance(value, bytes) else str(value)
    return datetime.fromisoformat(text)


async def _typical_minutes(redis: Redis) -> int | None:
    raw = await redis.lrange(_DURATIONS, 0, _KEEP - 1)
    values = sorted(int(v) for v in raw)
    if not values:
        return None
    return max(1, round(values[len(values) // 2] / 60))


async def mark_blocked(redis: Redis, cooldown_seconds: int) -> FsspBlock:
    now = _now()
    await redis.set(_SINCE, now.isoformat(), nx=True, ex=_TTL_SECONDS)
    await redis.set(_LAST, now.isoformat(), ex=_TTL_SECONDS)
    since = _parse(await redis.get(_SINCE)) or now
    return FsspBlock(
        since=since, retry_at=now + timedelta(seconds=cooldown_seconds), typical_minutes=await _typical_minutes(redis),
    )


async def mark_ok(redis: Redis) -> None:
    """Поиск снова прошёл — блокировка снята; запоминаем, сколько длилась."""
    since = _parse(await redis.get(_SINCE))
    if since is None:
        return
    seconds = int((_now() - since).total_seconds())
    await redis.lpush(_DURATIONS, seconds)
    await redis.ltrim(_DURATIONS, 0, _KEEP - 1)
    await redis.delete(_SINCE, _LAST)


async def active_block(redis: Redis, cooldown_seconds: int) -> FsspBlock | None:
    """Блокировка, во время которой сайт лучше не трогать; None — можно пробовать."""
    since = _parse(await redis.get(_SINCE))
    last = _parse(await redis.get(_LAST))
    if since is None or last is None:
        return None
    retry_at = last + timedelta(seconds=cooldown_seconds)
    if _now() >= retry_at:
        return None
    return FsspBlock(since=since, retry_at=retry_at, typical_minutes=await _typical_minutes(redis))
