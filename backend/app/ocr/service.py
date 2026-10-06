import asyncio
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog
import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Application, OcrResult
from app.ocr.base import KEY_FIELDS, OcrProvider
from app.ocr.name_match import names_match
from app.ocr.normalize import (
    normalize_date,
    normalize_department_code,
    normalize_passport_number,
    normalize_passport_series,
)

logger = structlog.get_logger()


class OcrProviderUnavailable(Exception):
    """Провайдер распознавания не ответил ни разу после всех попыток (ваше
    явное указание: сбой источника не должен останавливать заявку). Вызывающий
    код (app/engine/pipeline.py::run_stage_a) ловит именно этот тип — не любую
    Exception — и уводит заявку оператору, не маскируя настоящие баги."""

_NORMALIZERS = {
    "birth_date": normalize_date,
    "issue_date": normalize_date,
    "passport_series": normalize_passport_series,
    "passport_number": normalize_passport_number,
    "department_code": normalize_department_code,
}


@dataclass(frozen=True)
class OcrOutcome:
    ocr_result_id: Any
    below_threshold: bool
    name_mismatch: bool
    min_confidence: float
    # Уже распакованный плоский словарь ({"last_name": {"value":...}, ...}) —
    # НЕ то же самое, что OcrResult.fields (колонка БД, обёртка с "fields"/
    # "flags"/"below_threshold"). Для чтения уже сохранённой строки из БД —
    # app.engine.pipeline.unwrap_ocr_fields, не напрямую.
    fields: dict[str, dict[str, Any]]


def _load_ocr_threshold() -> float:
    settings = get_settings()
    path = Path(settings.ruleset_path)
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    try:
        return float(config["ocr"]["min_confidence_key_fields"])
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"конфиг {path} не содержит ocr.min_confidence_key_fields"
        ) from exc


def _normalize_fields(raw_fields) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for name, field in raw_fields.items():
        value = field.value
        normalizer = _NORMALIZERS.get(name)
        if normalizer is not None and value is not None:
            value = normalizer(value)
        normalized[name] = {"value": value, "confidence": field.confidence}
    return normalized


async def run_ocr(
    session: AsyncSession,
    provider: OcrProvider,
    *,
    application: Application,
    passport_main: bytes,
    passport_reg: bytes,
    max_attempts: int = 1,
    retry_delay_seconds: float = 0.0,
) -> OcrOutcome:
    """Распознаёт паспорт, нормализует поля, сверяет ФИО с анкетой и сохраняет
    результат в ocr_results. Проверки по распознанным данным — вне рамок (T05).

    max_attempts > 1 — провайдер может быть временно недоступен (сеть, ключ
    не настроен): несколько попыток подряд, и только после того, как ВСЕ они
    не удались, наружу уходит OcrProviderUnavailable (ваше явное указание —
    не держать заявку в статусе "ocr" бесконечно, не роняя при этом саму
    заявку на первом же сетевом сбое)."""
    raw = None
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            raw = await provider.recognize(passport_main, passport_reg)
            break
        except Exception as exc:  # noqa: BLE001 — провайдер может упасть как угодно, это не должно ронять заявку
            last_error = exc
            logger.warning(
                "ocr_provider_attempt_failed",
                application_id=str(application.id),
                provider=provider.name,
                attempt=attempt + 1,
                max_attempts=max_attempts,
                error=str(exc),
            )
            if attempt < max_attempts - 1 and retry_delay_seconds > 0:
                await asyncio.sleep(retry_delay_seconds)

    if raw is None:
        logger.error(
            "ocr_provider_unavailable",
            application_id=str(application.id),
            provider=provider.name,
            attempts=max_attempts,
        )
        raise OcrProviderUnavailable(str(last_error))

    normalized = _normalize_fields(raw.fields)

    key_confidences = [normalized[f]["confidence"] for f in KEY_FIELDS if f in normalized]
    min_confidence = min(key_confidences) if key_confidences else 0.0
    below_threshold = min_confidence < _load_ocr_threshold()

    form = application.profile or {}
    form_name = " ".join(
        part
        for part in (form.get("last_name"), form.get("first_name"), form.get("middle_name"))
        if part
    )
    ocr_name = " ".join(
        part
        for part in (
            normalized.get("last_name", {}).get("value"),
            normalized.get("first_name", {}).get("value"),
            normalized.get("middle_name", {}).get("value"),
        )
        if part
    )
    name_mismatch = not names_match(form_name, ocr_name)

    ocr_result = OcrResult(
        application_id=application.id,
        provider=provider.name,
        response_raw=raw.raw_response,
        fields={
            "fields": normalized,
            "flags": {
                "unreadable": raw.unreadable,
                "cropped": raw.cropped,
                "possible_edit": raw.possible_edit,
                "name_mismatch": name_mismatch,
            },
            "below_threshold": below_threshold,
        },
        min_confidence=Decimal(str(round(min_confidence, 3))),
    )
    session.add(ocr_result)
    await session.flush()

    # Персданные (ФИО, паспорт, адрес) в лог не попадают — только техническая сводка.
    logger.info(
        "ocr_completed",
        application_id=str(application.id),
        provider=provider.name,
        below_threshold=below_threshold,
        name_mismatch=name_mismatch,
        unreadable=raw.unreadable,
        cropped=raw.cropped,
        possible_edit=raw.possible_edit,
        min_confidence=round(min_confidence, 3),
    )

    return OcrOutcome(
        ocr_result_id=ocr_result.id,
        below_threshold=below_threshold,
        name_mismatch=name_mismatch,
        min_confidence=min_confidence,
        fields=normalized,
    )
