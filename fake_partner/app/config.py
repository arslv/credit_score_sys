from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # T18: интеграция по новой схеме — fake_partner теперь сама шлёт нам
    # заявки (имитирует заказчика) и принимает НАШИ исходящие уведомления.
    #
    # Токен, которым МЫ (dialog-scoring) обязаны представиться на
    # POST /partner/webhook — тот же, что наш PARTNER_OUTBOUND_TOKEN.
    expected_inbound_token: str = "dev-outbound-token"
    # Служебный токен для /_test/* эндпоинтов этого сервиса (как и раньше).
    fake_partner_token: str = "dev-token"
    # Адрес и токен НАШЕГО API — fake_partner стучится туда сама из
    # /_test/trigger-* (имитация «заказчик шлёт нам вебхук»).
    dialog_api_base_url: str = "http://api:8000"
    dialog_api_token: str = "dev-inbound-token"


@lru_cache
def get_settings() -> Settings:
    return Settings()
