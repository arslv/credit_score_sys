from dataclasses import dataclass
from typing import Any, Protocol

# Поля из раздела ТЗ «Анкета» -> «Распознаётся с фото (стадия А)».
PASSPORT_FIELDS = (
    "last_name",
    "first_name",
    "middle_name",
    "birth_date",
    "birth_place",
    "gender",
    "passport_series",
    "passport_number",
    "issue_date",
    "issued_by",
    "department_code",
    "registration_address",
)

# Ключевые поля для порога уверенности (ТЗ, «Стадия А, шаг 1. OCR»).
KEY_FIELDS = ("passport_series", "passport_number", "last_name", "first_name", "birth_date")


@dataclass(frozen=True)
class OcrFieldValue:
    value: str | None
    confidence: float


@dataclass(frozen=True)
class OcrRawResult:
    fields: dict[str, OcrFieldValue]
    unreadable: bool
    cropped: bool
    possible_edit: bool
    raw_response: dict[str, Any]
    """Ответ провайдера как пришёл — уходит в ocr_results.response_raw без изменений
    (правило 3 CLAUDE.md)."""


class OcrProvider(Protocol):
    """Общий интерфейс распознавания. Смена провайдера — один файл (ТЗ, «Архитектура»)."""

    name: str

    async def recognize(self, passport_main: bytes, passport_reg: bytes) -> OcrRawResult:
        """На вход — только разворот паспорта и страница регистрации. Селфи не передаётся."""
        ...
