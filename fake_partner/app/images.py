import io

from PIL import Image, ImageDraw, ImageFont

# Вымышленные данные на синтетических картинках — никаких реальных документов.
DOCUMENT_KINDS = ("selfie_passport", "passport_main", "passport_reg")

# Кириллица нужна для ФИО и адресов; bitmap-шрифт Pillow по умолчанию её не знает.
_CYRILLIC_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _load_font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype(_CYRILLIC_FONT_PATH, size=size)
    except OSError:
        return ImageFont.load_default()


_TITLE_FONT = _load_font(16)
_TEXT_FONT = _load_font(18)


def _render(title: str, lines: list[str]) -> bytes:
    image = Image.new("RGB", (640, 420), color=(235, 235, 240))
    draw = ImageDraw.Draw(image)

    draw.rectangle([(0, 0), (639, 419)], outline=(120, 120, 130), width=3)
    draw.text((20, 15), "SYNTHETIC DOCUMENT — NOT A REAL PASSPORT", fill=(200, 30, 30), font=_TITLE_FONT)
    draw.text((20, 45), title, fill=(20, 20, 20), font=_TEXT_FONT)

    y = 90
    for line in lines:
        draw.text((20, y), line, fill=(20, 20, 20), font=_TEXT_FONT)
        y += 28

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def render_passport_main(fields: dict) -> bytes:
    return _render(
        "Разворот паспорта (мок)",
        [
            f"Фамилия: {fields['last_name']}",
            f"Имя: {fields['first_name']}",
            f"Отчество: {fields.get('middle_name') or '-'}",
            f"Дата рождения: {fields['birth_date']}",
            f"Пол: {fields['gender']}",
            f"Серия: {fields['passport_series']}  Номер: {fields['passport_number']}",
            f"Дата выдачи: {fields['issue_date']}",
            f"Кем выдан: {fields['issued_by']}",
            f"Код подразделения: {fields['department_code']}",
        ],
    )


def render_passport_registration(fields: dict) -> bytes:
    return _render(
        "Страница регистрации (мок)",
        [
            f"Зарегистрирован(а) по адресу:",
            fields["registration_address"],
        ],
    )


def render_selfie_passport() -> bytes:
    return _render("Селфи с паспортом (мок)", ["Синтетическое изображение, лицо не изображено"])
