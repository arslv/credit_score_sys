from app.ocr.base import PASSPORT_FIELDS

_FIELD_SCHEMA = {
    "type": "object",
    "properties": {
        "value": {"type": ["string", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["value", "confidence"],
}

EXTRACT_TOOL_NAME = "extract_passport_fields"

EXTRACT_TOOL_SCHEMA = {
    "name": EXTRACT_TOOL_NAME,
    "description": (
        "Извлечь поля разворота паспорта РФ и страницы регистрации. "
        "Для каждого поля вернуть значение (или null, если не видно) и уверенность 0-1."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "fields": {
                "type": "object",
                "properties": {field: _FIELD_SCHEMA for field in PASSPORT_FIELDS},
                "required": list(PASSPORT_FIELDS),
            },
            "unreadable": {
                "type": "boolean",
                "description": "Фото нечитаемо целиком или в значимой части",
            },
            "cropped": {
                "type": "boolean",
                "description": "Разворот паспорта обрезан, не видны нужные поля",
            },
            "possible_edit": {
                "type": "boolean",
                "description": "Есть признаки редактирования/подделки изображения",
            },
        },
        "required": ["fields", "unreadable", "cropped", "possible_edit"],
    },
}

EXTRACT_INSTRUCTION = """\
Перед тобой два фото: (1) разворот паспорта РФ с фотографией и (2) страница регистрации \
(прописки) того же паспорта. Извлеки поля через инструмент extract_passport_fields.

Поля с разворота: last_name, first_name, middle_name, birth_date (в любом читаемом формате \
как на фото), birth_place, gender (male/female), passport_series, passport_number, \
issue_date, issued_by, department_code.
Поле со страницы регистрации: registration_address.

Если поле не видно или отсутствует (например, нет отчества) — value: null, confidence по \
факту видимости. Не придумывай значения. confidence — насколько ты уверен в точности \
распознанного значения, а не в его правдоподобии.
"""
