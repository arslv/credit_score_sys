"""Соответствие наших исходов статусам в системе заказчика — в конфиге, не в коде
(ТЗ, «Интеграция» -> «Соответствие статусов»; задача T04)."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from app.config import get_settings


class StatusMappingEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    partner_status: str
    comment_template: str


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _load_raw() -> dict[str, Any]:
    path = Path(get_settings().status_mapping_path)
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def get_status_mapping(our_status: str) -> StatusMappingEntry:
    raw = _load_raw()
    try:
        entry = raw[our_status]
    except KeyError as exc:
        raise ValueError(
            f"нет соответствия для статуса {our_status!r} в {get_settings().status_mapping_path}"
        ) from exc
    return StatusMappingEntry.model_validate(entry)


def render_comment(mapping: StatusMappingEntry, **context: Any) -> str:
    return mapping.comment_template.format_map(_SafeDict(context))
