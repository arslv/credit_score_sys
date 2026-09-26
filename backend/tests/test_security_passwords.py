"""T12: хеширование паролей (argon2id) — ТЗ «Аутентификация»."""

import time

from app.security.passwords import hash_password, verify_dummy_password, verify_password


def test_hash_is_not_plaintext_and_verifies():
    password_hash = hash_password("correct horse battery staple")
    assert password_hash != "correct horse battery staple"
    assert verify_password(password_hash, "correct horse battery staple") is True


def test_wrong_password_does_not_verify():
    password_hash = hash_password("correct horse battery staple")
    assert verify_password(password_hash, "wrong password") is False


def test_dummy_password_never_raises():
    verify_dummy_password("anything at all")  # не должно бросать


def test_dummy_and_real_verification_take_comparable_time():
    """Timing-safe ответ на "нет такого email" (ваше уточнение): дамми-хеш
    должен считаться сравнимое время с реальной проверкой, не in-memory
    заглушкой в духе "return False" за микросекунды."""
    real_hash = hash_password("some real password")

    start = time.perf_counter()
    verify_password(real_hash, "wrong guess")
    real_duration = time.perf_counter() - start

    start = time.perf_counter()
    verify_dummy_password("wrong guess")
    dummy_duration = time.perf_counter() - start

    # Оба — реальные вызовы argon2.verify с одинаковыми параметрами, разница
    # должна быть на порядки меньше самой операции (не "0.3с vs 0.001с").
    assert dummy_duration > real_duration * 0.3
