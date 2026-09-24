"""Выбор fake/http реализации внешних источников T09 по настройке
external_sources_mode (app.config.Settings). fake — безопасный дефолт: нет ни
ключей, ни подтверждённых URL, поэтому переключение на http — осознанное
действие оператора окружения, не поведение по умолчанию (тот же принцип, что
и registries_downloader в T08)."""

from app.config import Settings
from app.sources.fedresurs import FakeFedresursSource, FedresursHttpSource
from app.sources.fns import FakeFnsSource, FnsHttpSource
from app.sources.fssp import FakeFsspSource, FsspHttpSource, FsspSource


def build_fns_source(settings: Settings):
    if settings.external_sources_mode == "http":
        return FnsHttpSource(
            base_url=settings.fns_base_url,
            timeout_seconds=settings.fns_request_timeout_seconds,
            max_attempts=settings.fns_max_attempts,
        )
    return FakeFnsSource()


def build_fedresurs_source(settings: Settings):
    if settings.external_sources_mode == "http":
        # Всегда source_unavailable — см. app/sources/fedresurs.py.
        return FedresursHttpSource()
    return FakeFedresursSource()


def build_fssp_source(settings: Settings) -> FsspSource:
    if settings.external_sources_mode == "http":
        return FsspHttpSource(
            base_url=settings.fssp_base_url,
            api_key=settings.fssp_api_key,
            submit_timeout_seconds=settings.fssp_submit_timeout_seconds,
            submit_max_attempts=settings.fssp_submit_max_attempts,
        )
    return FakeFsspSource()
