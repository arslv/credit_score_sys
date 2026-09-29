"""Защита от случайного запуска фейковых/фикстурных провайдеров в бою (T11,
"три вещи" -> защита от фейков). APP_ENV по умолчанию — production
(app/config.py): забытая переменная должна означать самый строгий режим, а не
самый мягкий. Проверяется на старте api (app/main.py, на уровне модуля — до
того, как uvicorn начнёт принимать запросы) и worker/beat (app/celery_app.py:
тот же модуль импортируют обе Celery-команды, `celery -A app.celery_app`)."""

from app.config import Settings


class UnsafeProviderConfigurationError(RuntimeError):
    """APP_ENV=production, но включён хотя бы один fake/fixture провайдер."""


def assert_safe_for_environment(settings: Settings) -> None:
    if settings.app_env != "production":
        return

    violations: list[str] = []
    if settings.ocr_provider_mode != "anthropic":
        violations.append(
            f"распознавание паспорта настроено на {settings.ocr_provider_mode!r} — "
            "поправьте переменную окружения OCR_PROVIDER_MODE (в production — anthropic)"
        )
    if settings.bki_provider_mode == "fake":
        violations.append(
            "БКИ настроено на фейковые отчёты — поправьте переменную окружения "
            "BKI_PROVIDER_MODE (в production допустимо только not_configured, пока нет договора с бюро)"
        )
    if settings.external_sources_mode == "fake":
        violations.append(
            "ФССП/ФНС/Федресурс настроены на заглушки — поправьте переменную окружения "
            "EXTERNAL_SOURCES_MODE (в production — http)"
        )
    if not settings.cookie_secure:
        violations.append(
            "кука сессии администратора настроена БЕЗ Secure — поправьте переменную окружения "
            "COOKIE_SECURE (в production обязательно true; false допустимо только в dev — "
            "см. docs/deployment.md про Secure-куку на http://localhost)"
        )

    if violations:
        raise UnsafeProviderConfigurationError(
            "APP_ENV=production, но обнаружены фейковые провайдеры данных — сервис не запускается:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )
