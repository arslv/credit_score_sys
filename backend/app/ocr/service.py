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
) -> OcrOutcome:
    """Распознаёт паспорт, нормализует поля, сверяет ФИО с анкетой и сохраняет
    результат в ocr_results. Проверки по распознанным данным — вне рамок (T05)."""
    raw = await provider.recognize(passport_main, passport_reg)
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
