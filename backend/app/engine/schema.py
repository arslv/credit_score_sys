from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Operator(StrEnum):
    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"
    BETWEEN = "between"
    IN = "in"
    NOT_IN = "not_in"
    IS_NULL = "is_null"


_LIST_OPERATORS = (Operator.IN, Operator.NOT_IN)


class RuleDef(BaseModel):
    """Одно правило из hard_stops / stage_b_pre_bki / soft_rules.

    Доступ к полю — по пути через точку (checks.mvd.status, features.pti),
    см. app.engine.rules.get_field.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    field: str
    op: Operator
    value: Any = None
    reason: str | None = None
    action: str | None = None
    freeze: bool = False

    @model_validator(mode="after")
    def _check_value_shape(self) -> "RuleDef":
        if self.op is Operator.IS_NULL:
            if self.value is not None:
                raise ValueError(f"rule {self.id!r}: op 'is_null' не принимает value")
            return self

        if self.value is None:
            raise ValueError(f"rule {self.id!r}: op {self.op!r} требует value")

        if self.op is Operator.BETWEEN:
            if not isinstance(self.value, list) or len(self.value) != 2:
                raise ValueError(f"rule {self.id!r}: op 'between' требует value из двух элементов")
        elif self.op in _LIST_OPERATORS:
            if not isinstance(self.value, list) or not self.value:
                raise ValueError(f"rule {self.id!r}: op {self.op!r} требует непустой список value")
        return self


class OcrConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_confidence_key_fields: float = Field(ge=0, le=1)


class LimitsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_min: Decimal
    amount_max: Decimal
    term_min: int
    term_max: int
    pti_max: float
    declared_income_cap: Decimal

    @model_validator(mode="after")
    def _check_ranges(self) -> "LimitsConfig":
        if self.amount_min >= self.amount_max:
            raise ValueError("limits.amount_min должен быть меньше limits.amount_max")
        if self.term_min >= self.term_max:
            raise ValueError("limits.term_min должен быть меньше limits.term_max")
        return self


class BkiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cache_days: int = Field(ge=0)
    on_unavailable: str


class ScoreThresholdsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    auto_approve_above: float
    auto_reject_below: float

    @model_validator(mode="after")
    def _check_order(self) -> "ScoreThresholdsConfig":
        if self.auto_reject_below >= self.auto_approve_above:
            raise ValueError("score_thresholds.auto_reject_below должен быть меньше auto_approve_above")
        return self


class MatrixEntry(BaseModel):
    """Условие вида {amount_gt: 200000} и переопределение порогов/параметров сделки.
    Ключ when — поле [+ суффикс оператора], см. app.engine.rules.parse_matrix_key."""

    model_config = ConfigDict(extra="forbid")

    when: dict[str, Any]
    override: dict[str, Any]


class AntifraudConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_applications_per_phone_30d: int = Field(ge=1)
    duplicate_window_days: int = Field(ge=1)


class RosfinmonSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name_match_min_similarity: float = Field(ge=0, le=1)


class RegistriesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_staleness_days: int = Field(ge=1)


class PassportSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Справочник кодов подразделений — hflabs/fms-unit (config/reference/fms_unit.csv,
    # см. docs/registries.md и config/reference/fms_unit.SOURCE.md).
    department_code_check_enabled: bool
    # Дата выдачи раньше года бланка (цифры 3-4 серии) или позже него больше чем
    # на N лет — флаг оператору (bланки не лежат на складе десятилетиями), не
    # автоотказ. Год бланка позже текущего года — отдельный hard_stop
    # PASSPORT_BLANK_YEAR_FUTURE (структурно невалидная серия, не порог конфига).
    blank_year_max_issue_offset_years: int = Field(ge=0)


class SourcesConfig(BaseModel):
    """Пороги локальных источников стадии А, шаг 2 (ТЗ, «Источники данных», T08)."""

    model_config = ConfigDict(extra="forbid")

    rosfinmon: RosfinmonSourceConfig
    registries: RegistriesConfig
    passport: PassportSourceConfig


class RulesetConfig(BaseModel):
    """Полная схема YAML-конфига правил (ТЗ, «Конфиг правил и пороги»)."""

    model_config = ConfigDict(extra="forbid")

    version: int
    comment: str | None = None
    effective_from: date

    ocr: OcrConfig
    sources: SourcesConfig
    hard_stops: list[RuleDef] = Field(default_factory=list)
    stage_b_pre_bki: list[RuleDef] = Field(default_factory=list)
    soft_rules: list[RuleDef] = Field(default_factory=list)
    limits: LimitsConfig
    bki: BkiConfig
    score_thresholds: ScoreThresholdsConfig
    matrix: list[MatrixEntry] = Field(default_factory=list)
    antifraud: AntifraudConfig


def parse_ruleset(raw: dict[str, Any]) -> RulesetConfig:
    """Валидирует уже распарсенный YAML. Опечатка в ключе — ValidationError, не молчание."""
    return RulesetConfig.model_validate(raw)


def load_ruleset_from_yaml(path: str | Path) -> RulesetConfig:
    text = Path(path).read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    return parse_ruleset(raw)
