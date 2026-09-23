import re

from rapidfuzz import fuzz

from app.config import get_settings


def _normalize_for_match(name: str) -> str:
    name = name.casefold().replace("ё", "е")
    name = re.sub(r"\s+", " ", name).strip()
    return name


def names_match(form_name: str, ocr_name: str) -> bool:
    """Нечёткая сверка ФИО с допуском на опечатки (ТЗ, «Распознаётся с фото»)."""
    if not form_name.strip() or not ocr_name.strip():
        return False
    ratio = fuzz.ratio(_normalize_for_match(form_name), _normalize_for_match(ocr_name)) / 100
    return ratio >= get_settings().ocr_name_match_min_ratio
