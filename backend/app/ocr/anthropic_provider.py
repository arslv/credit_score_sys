import base64

from anthropic import AsyncAnthropic

from app.ocr.base import OcrFieldValue, OcrRawResult, PASSPORT_FIELDS
from app.ocr.schema import EXTRACT_INSTRUCTION, EXTRACT_TOOL_NAME, EXTRACT_TOOL_SCHEMA


def _detect_media_type(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def _image_block(data: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": _detect_media_type(data),
            "data": base64.standard_b64encode(data).decode("ascii"),
        },
    }


class AnthropicOcrProvider:
    """Распознавание через Anthropic Messages API: изображения в base64,
    структурированный ответ через tool use (ТЗ, «Архитектура» -> «Модуль распознавания»)."""

    def __init__(self, api_key: str, model: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self.name = f"anthropic:{model}"

    async def recognize(self, passport_main: bytes, passport_reg: bytes) -> OcrRawResult:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            tools=[EXTRACT_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": EXTRACT_TOOL_NAME},
            messages=[
                {
                    "role": "user",
                    "content": [
                        _image_block(passport_main),
                        _image_block(passport_reg),
                        {"type": "text", "text": EXTRACT_INSTRUCTION},
                    ],
                }
            ],
        )

        tool_use = next(block for block in response.content if block.type == "tool_use")
        payload = tool_use.input

        fields = {
            name: OcrFieldValue(
                value=(payload["fields"].get(name) or {}).get("value"),
                confidence=float((payload["fields"].get(name) or {}).get("confidence") or 0.0),
            )
            for name in PASSPORT_FIELDS
        }

        return OcrRawResult(
            fields=fields,
            unreadable=bool(payload.get("unreadable", False)),
            cropped=bool(payload.get("cropped", False)),
            possible_edit=bool(payload.get("possible_edit", False)),
            raw_response=payload,
        )
