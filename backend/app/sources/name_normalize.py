"""Нормализация ФИО перед сверкой по перечням (ТЗ, «Особенности сверки по перечням»):
регистр, ё->е, дефисы, распространённые орфографические варианты написания имён
арабского происхождения. Остальную вариативность добирает нечёткий поиск (pg_trgm)."""

import re

# Частые варианты написания одних и тех же звуков в именах арабского происхождения
# (актуально для Дагестана — см. ТЗ). Список заведомо неполный: расширяется по мере
# накопления практики, а не единоразовым большим словарём.
_VARIANT_SUBSTITUTIONS: tuple[tuple[str, str], ...] = (
    ("гъ", "г"),
    ("къ", "к"),
    ("хь", "х"),
    ("тс", "ц"),
    ("ъ", ""),
    ("ь", ""),
)


def normalize_name(raw: str) -> str:
    name = raw.strip().casefold()
    name = name.replace("ё", "е")
    name = re.sub(r"\s*-\s*", "-", name)  # дефисы без пробелов вокруг
    name = re.sub(r"\s+", " ", name)
    for variant, canonical in _VARIANT_SUBSTITUTIONS:
        name = name.replace(variant, canonical)
    return name


def full_name(last_name: str, first_name: str, middle_name: str | None = None) -> str:
    return " ".join(part for part in (last_name, first_name, middle_name) if part)
