"""Выбор fake/http реализации внешних источников по настройке
external_sources_mode (app.config.Settings). fake — безопасный дефолт для
ФНС/ФССП — оба бьют в настоящие публичные сайты без ключа (T16), переключение
на http — осознанное действие оператора окружения (тот же принцип, что и
registries_downloader в T08). Федресурс убран из конвейера целиком (T16, не
был настоящей интеграцией — всегда source_unavailable)."""

import json
from pathlib import Path

from app.config import Settings
from app.redis import get_redis
from app.sources.bki import BkiProvider, FakeBkiProvider, NotConfiguredBkiProvider, deserialize_report
from app.sources.fns import (
    FakeFnsEgripSource,
    FakeFnsInnSource,
    FakeFnsNpdSource,
    FnsEgripHttpSource,
    FnsInnHttpSource,
    FnsNpdHttpSource,
)
from app.sources.fssp import DevFsspCaptchaSource, FsspCaptchaHttpSource, FsspCaptchaSource
from app.sources.spectrum import SpectrumDataBkiProvider
from app.sources.spectrum_http import SpectrumHttpTransport


def build_fns_inn_source(settings: Settings):
    if settings.external_sources_mode == "http":
        return FnsInnHttpSource(
            redis=get_redis(),
            request_timeout_seconds=settings.fns_request_timeout_seconds,
            max_attempts=settings.fns_max_attempts,
            retry_backoff_seconds=settings.fns_retry_backoff_seconds,
            throttle_interval_seconds=settings.fns_shared_request_interval_seconds,
        )
    return FakeFnsInnSource()


def build_fns_npd_source(settings: Settings):
    if settings.external_sources_mode == "http":
        return FnsNpdHttpSource(
            redis=get_redis(),
            request_timeout_seconds=settings.fns_npd_request_timeout_seconds,
            throttle_interval_seconds=settings.fns_npd_min_interval_seconds,
        )
    return FakeFnsNpdSource()


def build_fns_egrip_source(settings: Settings):
    if settings.external_sources_mode == "http":
        return FnsEgripHttpSource(
            redis=get_redis(),
            request_timeout_seconds=settings.fns_request_timeout_seconds,
            max_attempts=settings.fns_max_attempts,
            retry_backoff_seconds=settings.fns_retry_backoff_seconds,
            throttle_interval_seconds=settings.fns_shared_request_interval_seconds,
        )
    return FakeFnsEgripSource()


def build_fssp_captcha_source(settings: Settings) -> FsspCaptchaSource:
    """Используется ТОЛЬКО из app/api/fssp.py по действию оператора (T16) —
    никогда из автоматического конвейера стадии А/Б. Режим — fssp_source_mode,
    а если он не задан — общий external_sources_mode."""
    if (settings.fssp_source_mode or settings.external_sources_mode) == "http":
        return FsspCaptchaHttpSource(
            redis=get_redis(),
            request_timeout_seconds=settings.fssp_request_timeout_seconds,
            throttle_interval_seconds=settings.fssp_min_request_interval_seconds,
        )
    # Режим разработки: читаемая капча с кодом 000000 (см. DevFsspCaptchaSource).
    return DevFsspCaptchaSource()


def build_bki_provider(settings: Settings) -> BkiProvider:
    if settings.bki_provider_mode == "fake":
        reports = {}
        if settings.bki_fixture_path and Path(settings.bki_fixture_path).exists():
            raw = json.loads(Path(settings.bki_fixture_path).read_text(encoding="utf-8"))
            reports = {key: deserialize_report(value) for key, value in raw.items()}
        return FakeBkiProvider(reports=reports)
    if settings.bki_provider_mode == "spectrum" and settings.spectrum_user and settings.spectrum_password:
        return SpectrumDataBkiProvider(build_spectrum_transport(settings))
    return NotConfiguredBkiProvider()


def build_spectrum_transport(settings: Settings) -> SpectrumHttpTransport:
    return SpectrumHttpTransport(
        base_url=settings.spectrum_api_url,
        user=settings.spectrum_user,
        password=settings.spectrum_password,
        pending=get_redis(),
        token_age_seconds=settings.spectrum_token_age_seconds,
        clock_skew_seconds=settings.spectrum_clock_skew_seconds,
        request_timeout_seconds=settings.spectrum_request_timeout_seconds,
        poll_interval_seconds=settings.spectrum_poll_interval_seconds,
        poll_timeout_seconds=settings.spectrum_poll_timeout_seconds,
    )
