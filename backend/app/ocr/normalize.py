import re
from datetime import datetime

_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y")


def normalize_date(raw: str | None) -> str | None:
    """Даты в ISO (YYYY-MM-DD). Нераспознанный формат — None, а не догадка."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalize_passport_series(raw: str | None) -> str | None:
    """Серия — 4 цифры."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) == 4 else None


def normalize_passport_number(raw: str | None) -> str | None:
    """Номер — 6 цифр."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) == 6 else None


def normalize_department_code(raw: str | None) -> str | None:
    """Код подразделения — XXX-XXX."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) != 6:
        return None
    return f"{digits[:3]}-{digits[3:]}"
