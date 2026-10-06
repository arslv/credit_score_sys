"""Код региона ФССП (T16) — ТОЛЬКО по адресу регистрации (ваше явное
указание: серия паспорта — код места ВЫДАЧИ, не места регистрации, для этого
не используется вообще). Таблица — config/fssp_regions.yaml, снята целиком с
сайта ФССП (app/sources/fssp.py)."""

import re
from functools import lru_cache
from pathlib import Path

import yaml

_GENERIC_WORDS = {
    "респ", "республика", "обл", "область", "край", "автономный", "округ", "ао",
    "г", "город", "и", "федеральная", "территория", "городской",
}


@lru_cache
def load_fssp_regions(path: str) -> dict[str, str]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return dict(raw["regions"])


def _significant_words(text: str) -> set[str]:
    words = re.findall(r"[а-яёa-z]+", text.lower())
    return {w for w in words if w not in _GENERIC_WORDS and len(w) > 2}


def match_region_code(address: str | None, regions: dict[str, str]) -> str | None:
    """Региона нет в адресе или совпадения нет -> None (оператор выбирает сам
    из выпадающего списка, ваше явное указание — 05 не подставляется молча).
    Совпадением считается вхождение ВСЕХ значимых слов названия региона в
    текст адреса (не частичное — иначе "Карачаево-Черкесия" ложно сматчится
    на упоминание одного только "Черкесска")."""
    if not address:
        return None
    address_words = _significant_words(address)
    if not address_words:
        return None

    best_code: str | None = None
    best_score = 0
    for code, label in regions.items():
        label_words = _significant_words(label)
        if label_words and label_words <= address_words and len(label_words) > best_score:
            best_score = len(label_words)
            best_code = code
    return best_code
