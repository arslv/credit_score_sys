from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://dialog:dialog@db:5432/dialog"
    redis_url: str = "redis://redis:6379/0"
    log_level: str = "INFO"

    environment: str = "development"

    # T11: защита от случайных фейковых провайдеров в бою (app/startup_guard.py).
    # По умолчанию — production, самый строгий режим: забытая переменная не
    # должна тихо означать самый мягкий. dev/test — permissive.
    app_env: Literal["dev", "test", "production"] = "production"

    # Интеграция с системой заказчика (см. fake_partner на этапе, пока их API неизвестен)
    partner_base_url: str = "http://fake_partner:8000"
    partner_token: str = "dev-token"
    partner_poll_interval_seconds: int = 30

    # Повторы обращений к их API при недоступности
    integration_retry_max_attempts: int = 5
    integration_retry_base_delay_seconds: float = 1.0
    integration_retry_max_delay_seconds: float = 30.0

    # Файловое хранилище фото (локальный каталог, за интерфейсом Storage)
    storage_dir: str = "/data/storage"

    # Распознавание фото паспорта (T05)
    anthropic_api_key: str = ""
    ocr_model: str = "claude-sonnet-5"
    # anthropic (по умолчанию, реальный ключ) | fixture — для scripts/e2e_check.py:
    # прогон через настоящие Celery-задачи без реального ключа Anthropic,
    # см. app/ocr/factory.py.
    ocr_provider_mode: str = "anthropic"
    ocr_fixture_path: str = ""
    # Технический порог нечёткого сравнения ФИО (допуск на опечатки) — не бизнес-правило
    # вида "возраст/сумма/срок" из правила 2 CLAUDE.md, поэтому не в YAML-конфиге.
    ocr_name_match_min_ratio: float = 0.85

    # Движок правил (T06) — активная версия выбирается по effective_from в ruleset_versions
    ruleset_path: str = "config/rulesets/dev.yaml"

    # Соответствие наших исходов статусам в системе заказчика — в конфиге, не в коде
    # (ТЗ, «Интеграция» -> «Соответствие статусов»)
    status_mapping_path: str = "config/status_mapping.yaml"

    # Оркестратор стадии А (T07)
    stage_a_poll_interval_seconds: int = 15

    # Синхронизатор реестров МВД/Росфинмониторинга (T08) — файлы кладутся сюда
    # вручную/полуавтоматически, см. app/registries/downloader.py
    registries_dir: str = "/data/registries"
    registries_sync_interval_seconds: int = 86400  # ежесуточно, как публикуют источники
    # local — читает файлы из registries_dir (по умолчанию, надёжный вариант);
    # http — качает по URL из *_registry_url. Рабочие URL не подтверждены (см.
    # app/registries/downloader.py) — просто так переключать в http нельзя.
    registries_downloader: str = "local"
    mvd_registry_url: str = ""
    rosfinmon_registry_url: str = ""

    # Справочник кодов подразделений (T08) — hflabs/fms-unit, статичный файл,
    # не синхронизируется периодически (см. docs/registries.md).
    department_codes_path: str = "config/reference/fms_unit.csv"

    # ФССП/ФНС/Федресурс (T09): таймауты/повторы — технические параметры, не
    # бизнес-правило (правило 2 CLAUDE.md касается порогов вида возраст/сумма/
    # срок; ср. ocr_name_match_min_ratio выше). Числа — из таблицы ТЗ «Стадия А,
    # шаг 3». Классификация типов производств ФССП, в отличие от этого, —
    # business-правило и живёт в YAML-конфиге (sources.fssp.classification).
    external_sources_mode: str = "fake"  # fake (по умолчанию) | http — см. app/sources/*.py
    fns_base_url: str = ""
    fns_request_timeout_seconds: float = 10.0
    fns_max_attempts: int = 2
    fedresurs_request_timeout_seconds: float = 20.0
    fedresurs_max_attempts: int = 2
    fssp_base_url: str = ""
    fssp_api_key: str = ""
    fssp_submit_timeout_seconds: float = 10.0
    fssp_submit_max_attempts: int = 3
    fssp_poll_interval_seconds: int = 5
    fssp_poll_deadline_seconds: int = 120  # "120 с на опрос" из таблицы ТЗ
    # Потолок на ВЕСЬ внешний блок шага 3 целиком (ФНС + Федресурс + ожидание
    # ФССП) — отдельно от таймаутов конкретных запросов, чтобы сумма ретраев не
    # растягивала стадию А на неопределённое время.
    stage_a_external_checks_deadline_seconds: float = 180.0

    # БКИ (T11): договора с бюро нет и неизвестно, с каким бюро он будет —
    # отдельная настройка от external_sources_mode (у ФССП/ФНС хотя бы есть
    # гипотетический URL, у БКИ нет и этого). not_configured — единственный
    # безопасный дефолт; fake — для дев/тестов с синтетическими отчётами.
    bki_provider_mode: str = "not_configured"  # not_configured (по умолчанию) | fake
    # Отчёты для FakeBkiProvider по сценарию — JSON {"Фамилия|Имя|ГГГГ-ММ-ДД": {...}}
    # в формате app.sources.bki.serialize_report. Пусто -> пустой FakeBkiProvider
    # (как раньше), см. app/sources/factory.py. Для scripts/e2e_check.py.
    bki_fixture_path: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
