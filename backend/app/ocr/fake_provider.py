from app.ocr.base import OcrFieldValue, OcrRawResult, PASSPORT_FIELDS

_DEFAULT_VALUES = {
    "last_name": "Иванов",
    "first_name": "Иван",
    "middle_name": "Иванович",
    "birth_date": "1990-01-15",
    "birth_place": "г. Тестоград",
    "gender": "male",
    # Год бланка "00" -> 2000 (безопасно в прошлом для любых обозримых дат теста,
    # T08) и регион "77" совпадает с department_code по умолчанию ниже.
    "passport_series": "7700",
    "passport_number": "567890",
    "issue_date": "2015-03-10",
    "issued_by": "УМВД России по тестовому региону",
    "department_code": "770-001",
    "registration_address": "г. Тестоград, ул. Примерная, д. 1, кв. 1",
}


def build_ocr_result(
    *,
    confidence: float = 0.98,
    unreadable: bool = False,
    cropped: bool = False,
    possible_edit: bool = False,
    overrides: dict[str, str | None] | None = None,
    confidence_overrides: dict[str, float] | None = None,
) -> OcrRawResult:
    """Собирает заранее заданный результат распознавания для тестов."""
    values = {**_DEFAULT_VALUES, **(overrides or {})}
    confidences = {**{f: confidence for f in PASSPORT_FIELDS}, **(confidence_overrides or {})}

    fields = {
        name: OcrFieldValue(value=values.get(name), confidence=confidences[name])
        for name in PASSPORT_FIELDS
    }
    return OcrRawResult(
        fields=fields,
        unreadable=unreadable,
        cropped=cropped,
        possible_edit=possible_edit,
        raw_response={
            "fields": {name: {"value": f.value, "confidence": f.confidence} for name, f in fields.items()},
            "unreadable": unreadable,
            "cropped": cropped,
            "possible_edit": possible_edit,
        },
    )


class FakeOcrProvider:
    """Возвращает заранее заданный JSON — для тестов и локального прогона без ключа."""

    def __init__(self, result: OcrRawResult | None = None) -> None:
        self.name = "fake"
        self._result = result or build_ocr_result()
        self.calls: list[tuple[bytes, bytes]] = []

    async def recognize(self, passport_main: bytes, passport_reg: bytes) -> OcrRawResult:
        self.calls.append((passport_main, passport_reg))
        return self._result
