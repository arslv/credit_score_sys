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
from app.sources.fssp import FakeFsspCaptchaSource, FsspCaptchaHttpSource, FsspCaptchaSource


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
    никогда из автоматического конвейера стадии А/Б."""
    if settings.external_sources_mode == "http":
        return FsspCaptchaHttpSource(
            redis=get_redis(),
            request_timeout_seconds=settings.fssp_request_timeout_seconds,
            throttle_interval_seconds=settings.fssp_min_request_interval_seconds,
        )
    return FakeFsspCaptchaSource()


def build_bki_provider(settings: Settings) -> BkiProvider:
    if settings.bki_provider_mode == "fake":
        reports = {}
        if settings.bki_fixture_path and Path(settings.bki_fixture_path).exists():
            raw = json.loads(Path(settings.bki_fixture_path).read_text(encoding="utf-8"))
            reports = {key: deserialize_report(value) for key, value in raw.items()}
        return FakeBkiProvider(reports=reports)
    return NotConfiguredBkiProvider()
