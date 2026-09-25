"""Выбор провайдера распознавания по настройке ocr_provider_mode — тот же
принцип, что app/sources/factory.py для внешних источников/БКИ. anthropic —
безопасный дефолт (не меняет поведение до этой правки); fixture — для
scripts/e2e_check.py, где реальный API-ключ не нужен и не должен требоваться."""

from app.config import Settings
from app.ocr.anthropic_provider import AnthropicOcrProvider
from app.ocr.fixture_provider import FixtureOcrProvider


def build_ocr_provider(settings: Settings):
    if settings.ocr_provider_mode == "fixture":
        return FixtureOcrProvider(settings.ocr_fixture_path)
    return AnthropicOcrProvider(settings.anthropic_api_key, settings.ocr_model)
