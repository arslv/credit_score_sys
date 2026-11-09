import asyncio

from redis.asyncio import Redis

from app.config import get_settings

# Клиент привязан к циклу событий, в котором создан: его соединения нельзя
# использовать из другого цикла. В api цикл один на весь процесс; в Celery
# каждая задача — свой asyncio.run(), и клиент из прошлой задачи падает с
# «Event loop is closed» (так ломались ИНН и Спектрум со второй задачи
# воркера). Поэтому — свой клиент на каждый цикл.
_redis: Redis | None = None
_redis_loop: asyncio.AbstractEventLoop | None = None


def get_redis() -> Redis:
    global _redis, _redis_loop
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _redis is None or _redis_loop is not loop:
        _redis = Redis.from_url(get_settings().redis_url)
        _redis_loop = loop
    return _redis
