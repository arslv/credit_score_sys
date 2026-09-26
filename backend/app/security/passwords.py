"""Хеширование паролей — argon2id (T12, ТЗ «Аутентификация»: "пароли хранятся
хешированными", задача допускает argon2 или bcrypt)."""

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()

# Для сверки времени ответа, когда email не найден вовсе (см.
# app/security/auth_service.py) — считаем хеш заведомо несуществующего пароля,
# чтобы "нет такого email" и "неверный пароль" занимали одинаковое время и не
# давали злоумышленнику разведать список сотрудников по задержке ответа.
_DUMMY_HASH = _hasher.hash("this-is-not-a-real-password-just-for-timing")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
        return True
    except VerifyMismatchError:
        return False


def verify_dummy_password(password: str) -> None:
    """Не возвращает результат — вызывается только ради временной задержки,
    когда учётной записи с таким email не существует."""
    try:
        _hasher.verify(_DUMMY_HASH, password)
    except VerifyMismatchError:
        pass
