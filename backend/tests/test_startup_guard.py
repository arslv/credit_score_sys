"""T11 ("три вещи" -> защита от фейков в бою): APP_ENV=production отказывается
стартовать с фейковыми/фикстурными провайдерами — забытая переменная должна
означать самый строгий режим, не самый мягкий."""

import pytest

from app.config import Settings
from app.startup_guard import UnsafeProviderConfigurationError, assert_safe_for_environment


def _settings(
    *, app_env: str, ocr_provider_mode: str = "anthropic", bki_provider_mode: str = "not_configured",
    external_sources_mode: str = "http", cookie_secure: bool = True,
    partner_ip_allowlist: str = "203.0.113.10", partner_photo_allow_http: bool = False,
) -> Settings:
    """Всегда передаёт все режимы явно — иначе тест зависел бы от того, что
    уже выставлено в окружении процесса (docker compose exec и т.п. — dev-
    compose, например, держит COOKIE_SECURE=false для всех контейнеров)."""
    return Settings(
        app_env=app_env, ocr_provider_mode=ocr_provider_mode,
        bki_provider_mode=bki_provider_mode, external_sources_mode=external_sources_mode,
        cookie_secure=cookie_secure, partner_ip_allowlist=partner_ip_allowlist,
        partner_photo_allow_http=partner_photo_allow_http,
    )


def test_production_with_fake_bki_refuses_to_start():
    settings = _settings(app_env="production", bki_provider_mode="fake")
    with pytest.raises(UnsafeProviderConfigurationError) as exc_info:
        assert_safe_for_environment(settings)
    assert "BKI_PROVIDER_MODE" in str(exc_info.value)


def test_production_with_fixture_ocr_refuses_to_start():
    settings = _settings(app_env="production", ocr_provider_mode="fixture")
    with pytest.raises(UnsafeProviderConfigurationError) as exc_info:
        assert_safe_for_environment(settings)
    assert "OCR_PROVIDER_MODE" in str(exc_info.value)


def test_production_with_fake_external_sources_refuses_to_start():
    settings = _settings(app_env="production", external_sources_mode="fake")
    with pytest.raises(UnsafeProviderConfigurationError) as exc_info:
        assert_safe_for_environment(settings)
    assert "EXTERNAL_SOURCES_MODE" in str(exc_info.value)


def test_production_with_insecure_cookie_refuses_to_start():
    settings = _settings(app_env="production", cookie_secure=False)
    with pytest.raises(UnsafeProviderConfigurationError) as exc_info:
        assert_safe_for_environment(settings)
    assert "COOKIE_SECURE" in str(exc_info.value)


def test_production_lists_every_violation_at_once():
    settings = _settings(
        app_env="production", bki_provider_mode="fake", ocr_provider_mode="fixture",
        external_sources_mode="fake", cookie_secure=False,
    )
    with pytest.raises(UnsafeProviderConfigurationError) as exc_info:
        assert_safe_for_environment(settings)
    message = str(exc_info.value)
    assert "BKI_PROVIDER_MODE" in message
    assert "OCR_PROVIDER_MODE" in message
    assert "EXTERNAL_SOURCES_MODE" in message
    assert "COOKIE_SECURE" in message


def test_production_with_empty_partner_ip_allowlist_refuses_to_start():
    """T21: в production вебхуки заказчика — только с их адресов."""
    for allowlist in ("", " , "):
        settings = _settings(app_env="production", partner_ip_allowlist=allowlist)
        with pytest.raises(UnsafeProviderConfigurationError) as exc_info:
            assert_safe_for_environment(settings)
        assert "PARTNER_IP_ALLOWLIST" in str(exc_info.value)


def test_production_with_http_photo_links_refuses_to_start():
    settings = _settings(app_env="production", partner_photo_allow_http=True)
    with pytest.raises(UnsafeProviderConfigurationError) as exc_info:
        assert_safe_for_environment(settings)
    assert "PARTNER_PHOTO_ALLOW_HTTP" in str(exc_info.value)


def test_dev_allows_empty_allowlist_and_http_photos():
    assert_safe_for_environment(_settings(app_env="dev", partner_ip_allowlist="", partner_photo_allow_http=True))


def test_production_with_real_providers_starts_fine():
    settings = _settings(app_env="production")  # anthropic/not_configured/http — все дефолты безопасны
    assert_safe_for_environment(settings)  # не бросает


def test_dev_allows_fake_providers():
    settings = _settings(
        app_env="dev", bki_provider_mode="fake", ocr_provider_mode="fixture", external_sources_mode="fake",
    )
    assert_safe_for_environment(settings)  # не бросает — dev permissive


def test_test_env_allows_fake_providers():
    settings = _settings(app_env="test", bki_provider_mode="fake", external_sources_mode="fake")
    assert_safe_for_environment(settings)  # не бросает — test permissive


def test_app_env_defaults_to_production_when_unset(monkeypatch):
    """Забытая переменная -> самый строгий режим, а не самый мягкий."""
    monkeypatch.delenv("APP_ENV", raising=False)
    settings = Settings(bki_provider_mode="fake")  # app_env не передан явно
    assert settings.app_env == "production"
    with pytest.raises(UnsafeProviderConfigurationError):
        assert_safe_for_environment(settings)


def test_app_env_rejects_unknown_value():
    with pytest.raises(Exception):  # pydantic ValidationError — опечатка ловится сразу
        Settings(app_env="produciton")
