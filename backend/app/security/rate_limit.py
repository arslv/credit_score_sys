"""Блокировка входа по IP (T12, ваше уточнение): независимо от блокировки по
учётной записи — иначе кто угодно, зная чужой email, держит его заблокированным
бесконечно, просто продолжая слать неверные пароли с любого IP. Redis —
простой счётчик с TTL, не требует новой таблицы под сугубо технические,
самоочищающиеся данные."""

from redis.asyncio import Redis

from app.config import get_settings


def _key(ip: str) -> str:
    return f"login_attempts:ip:{ip}"


async def register_failed_login_and_check_ip_blocked(redis: Redis, ip: str) -> bool:
    """Регистрирует неудачную попытку с этого IP и возвращает True, если IP
    уже заблокирован (в пределах окна набрано слишком много неудач).
    Намеренно не сбрасывается успешным входом — иначе один успешный логин на
    своём аккаунте снимал бы лимит для перебора ЧУЖИХ аккаунтов с того же IP."""
    settings = get_settings()
    key = _key(ip)
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, settings.login_ip_lockout_window_minutes * 60)
    return count > settings.login_ip_max_failed_attempts


async def is_ip_blocked(redis: Redis, ip: str) -> bool:
    settings = get_settings()
    count = await redis.get(_key(ip))
    return count is not None and int(count) > settings.login_ip_max_failed_attempts
