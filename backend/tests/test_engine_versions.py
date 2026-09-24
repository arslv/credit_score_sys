from datetime import datetime, timedelta, timezone

from app.engine.schema import RulesetConfig
from app.engine.versions import get_active_ruleset_version, load_config, save_ruleset_version


def _config(effective_from: str) -> RulesetConfig:
    return RulesetConfig.model_validate(
        {
            # Информационная метка из YAML — не то же самое, что настоящий номер
            # версии в БД (тот назначается автоматически, см. save_ruleset_version).
            "version": 1,
            "effective_from": effective_from,
            "ocr": {"min_confidence_key_fields": 0.85},
            "sources": {
                "rosfinmon": {"name_match_min_similarity": 0.7},
                "registries": {"max_staleness_days": 2},
                "passport": {
                    "department_code_check_enabled": False,
                    "blank_year_max_issue_offset_years": 3,
                },
            },
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
            "antifraud": {"max_applications_per_phone_30d": 3, "duplicate_window_days": 30},
        }
    )


async def test_save_assigns_sequential_version_automatically(db_session):
    """version в БД назначается сам (MAX+1) — не берётся из config.version.
    Иначе автосинхронизация с файлом (T08) конфликтовала бы по unique(version)
    каждый раз, когда кто-то забыл бы поднять число в YAML."""
    config = _config("2026-01-01")
    row_1 = await save_ruleset_version(db_session, config)
    row_2 = await save_ruleset_version(db_session, config)

    assert row_2.version == row_1.version + 1


async def test_save_and_load_round_trip(db_session):
    config = _config("2026-01-01")
    row = await save_ruleset_version(db_session, config)

    assert row.effective_from == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert len(row.config_hash) == 64  # sha256 hex

    loaded = load_config(row)
    assert loaded.version == config.version  # информационное поле сохранилось как есть
    assert loaded.limits.amount_min == config.limits.amount_min


async def test_active_version_picked_by_effective_from(db_session):
    now = datetime.now(timezone.utc)
    # "recent" — ровно сегодня, гарантированно позже любых "вчерашних" строк,
    # которые могли остаться в таблице от других тестов.
    past = _config((now - timedelta(days=10)).date().isoformat())
    recent = _config(now.date().isoformat())
    future = _config((now + timedelta(days=10)).date().isoformat())

    await save_ruleset_version(db_session, past)
    recent_row = await save_ruleset_version(db_session, recent)
    await save_ruleset_version(db_session, future)

    active = await get_active_ruleset_version(db_session, at=now)
    assert active.version == recent_row.version


async def test_future_version_is_not_active_yet(db_session):
    now = datetime.now(timezone.utc)
    future = _config((now + timedelta(days=1)).date().isoformat())
    future_row = await save_ruleset_version(db_session, future)

    active = await get_active_ruleset_version(db_session, at=now)
    assert active is None or active.version != future_row.version
