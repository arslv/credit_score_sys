"""Автосинхронизация config/rulesets/*.yaml -> ruleset_versions при старте
приложения (T08). Таблица ruleset_versions общая на весь прогон тестов (пишем
реальными коммитами через session_factory — см. conftest), поэтому каждый тест
метит свою версию уникальным comment и берёт effective_from из широкого
случайного диапазона, чтобы не зависеть от порядка выполнения и не столкнуться
с другими тестами, использующими фиксированные смещения (например, "вчера")."""

import itertools
import uuid
from datetime import date, timedelta
from pathlib import Path

import yaml

from app.engine.schema import parse_ruleset
from app.engine.versions import (
    RulesetSyncResult,
    get_active_ruleset_version,
    load_config,
    save_ruleset_version,
    sync_ruleset_from_file,
)
from app.models import Employee

# Общий на файл счётчик, монотонно приближающийся к "сегодня": каждый следующий
# вызов получает effective_from на день ближе к текущей дате, чем предыдущий.
# Это гарантирует (а) отсутствие коллизий дат между тестами и вызовами внутри
# теста, (б) что порядок "кто новее" всегда совпадает с порядком выполнения —
# без этого случайные даты из широкого диапазона иногда бы давали ничью или
# обращали порядок при сравнении с effective_from DESC LIMIT 1. 500 — с большим
# запасом от "-1 день", которым пользуется active_ruleset в test_engine_pipeline.py.
_days_ago_counter = itertools.count(500, -1)


def _marker() -> str:
    return f"test-marker-{uuid.uuid4().hex}"


def _unique_effective_from() -> str:
    days_ago = next(_days_ago_counter)
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _valid_config_dict(comment: str, **overrides) -> dict:
    base = {
        "version": 1,
        "comment": comment,
        "effective_from": _unique_effective_from(),
        "ocr": {"min_confidence_key_fields": 0.85},
        "sources": {
            "rosfinmon": {"name_match_min_similarity": 0.7},
            "registries": {"max_staleness_days": 2},
            "passport": {
                "department_code_check_enabled": False,
                "blank_year_max_issue_offset_years": 3,
            },
            "fssp": {"classification": []},
        },
        "hard_stops": [],
        "stage_b_pre_bki": [],
        "soft_rules": [],
        "limits": {
            "amount_min": 15000,
            "amount_max": 500000,
            "term_min": 3,
            "term_max": 24,
            "pti_max": 0.60,
            "declared_income_cap": 80000,
        },
        "bki": {"cache_days": 30, "on_unavailable": "manual_review"},
        "score_thresholds": {"enabled": True, "auto_approve_above": 700, "auto_reject_below": 450},
        "matrix": [],
        "antifraud": {"max_applications_per_phone_30d": 3, "duplicate_window_days": 30},
    }
    base.update(overrides)
    return base


def _write_yaml(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "ruleset.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


async def test_sync_creates_version_from_file(session_factory, tmp_path):
    marker = _marker()
    path = _write_yaml(tmp_path, _valid_config_dict(marker))

    result = await sync_ruleset_from_file(session_factory, path)

    assert result == RulesetSyncResult.CREATED
    async with session_factory() as session:
        active = await get_active_ruleset_version(session)
        assert load_config(active).comment == marker
        assert active.author_id is None  # author "system"


async def test_sync_unchanged_when_hash_matches(session_factory, tmp_path):
    marker = _marker()
    path = _write_yaml(tmp_path, _valid_config_dict(marker))

    first = await sync_ruleset_from_file(session_factory, path)
    second = await sync_ruleset_from_file(session_factory, path)

    assert first == RulesetSyncResult.CREATED
    assert second == RulesetSyncResult.UNCHANGED

    async with session_factory() as session:
        active = await get_active_ruleset_version(session)
        assert load_config(active).comment == marker  # версия не задублировалась


async def test_sync_creates_new_version_when_file_changes_without_human_author(session_factory, tmp_path):
    path = _write_yaml(tmp_path, _valid_config_dict(_marker()))
    await sync_ruleset_from_file(session_factory, path)

    marker_2 = _marker()
    _write_yaml(tmp_path, _valid_config_dict(marker_2))  # тот же путь, новое содержимое

    result = await sync_ruleset_from_file(session_factory, path)

    assert result == RulesetSyncResult.CREATED
    async with session_factory() as session:
        active = await get_active_ruleset_version(session)
        assert active.author_id is None
        assert load_config(active).comment == marker_2


async def test_sync_file_invalid_does_not_touch_active_version(session_factory, tmp_path):
    marker = _marker()
    path = _write_yaml(tmp_path, _valid_config_dict(marker))
    assert await sync_ruleset_from_file(session_factory, path) == RulesetSyncResult.CREATED

    async with session_factory() as session:
        before = await get_active_ruleset_version(session)

    broken = _valid_config_dict(_marker())
    del broken["limits"]
    _write_yaml(tmp_path, broken)

    result = await sync_ruleset_from_file(session_factory, path)

    assert result == RulesetSyncResult.FILE_INVALID
    async with session_factory() as session:
        after = await get_active_ruleset_version(session)
        assert after.version == before.version
        assert load_config(after).comment == marker


async def test_sync_malformed_yaml_syntax_does_not_touch_active_version(session_factory, tmp_path):
    marker = _marker()
    path = _write_yaml(tmp_path, _valid_config_dict(marker))
    assert await sync_ruleset_from_file(session_factory, path) == RulesetSyncResult.CREATED

    path.write_text("version: 1\nhard_stops: [this is not: valid", encoding="utf-8")

    result = await sync_ruleset_from_file(session_factory, path)

    assert result == RulesetSyncResult.FILE_INVALID
    async with session_factory() as session:
        active = await get_active_ruleset_version(session)
        assert load_config(active).comment == marker


async def test_sync_conflict_does_not_touch_human_authored_active_version(session_factory, tmp_path):
    """Идёт последним в файле: как только активная версия становится
    человеко-авторской, все ПОСЛЕДУЮЩИЕ попытки sync_ruleset_from_file с другим
    содержимым файла будут CONFLICT (пока человек явно не заменит версию) — это
    осознанное поведение, а не баг, но оно означает, что тесты после этого не
    могут больше полагаться на "system"-цепочку версий."""
    marker_human = _marker()
    async with session_factory() as session:
        employee = Employee(
            full_name="Тестовый Риск-менеджер",
            email=f"test-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="x",
            role="risk_manager",
        )
        session.add(employee)
        await session.flush()

        human_config = parse_ruleset(_valid_config_dict(marker_human))
        human_row = await save_ruleset_version(session, human_config, author_id=employee.id)
        await session.commit()

    path = _write_yaml(tmp_path, _valid_config_dict(_marker()))

    result = await sync_ruleset_from_file(session_factory, path)

    assert result == RulesetSyncResult.CONFLICT
    async with session_factory() as session:
        active = await get_active_ruleset_version(session)
        assert active.version == human_row.version
        assert load_config(active).comment == marker_human  # ручная версия не тронута
