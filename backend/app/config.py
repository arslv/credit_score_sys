from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://dialog:dialog@db:5432/dialog"
    redis_url: str = "redis://redis:6379/0"
    log_level: str = "INFO"

    environment: str = "development"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
