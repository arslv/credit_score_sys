from datetime import date, datetime, timezone

import pytest
from sqlalchemy import func, select

from app.models import SanctionsList
from app.sources.base import CheckStatus, Subject
from app.sources.name_normalize import full_name, normalize_name
from app.sources.rosfinmon import RosfinmonSource


async def _seed_entry(session, *, last_name, first_name, middle_name, birth_date, list_type="te"):
    name = full_name(last_name, first_name, middle_name)
    entry = SanctionsList(
        list_type=list_type,
        full_name=name,
        name_normalized=normalize_name(name),
        birth_date=birth_date,
        loaded_at=datetime.now(timezone.utc),
    )
    session.add(entry)
    await session.flush()
    return entry


# --- варианты написания одного и того же человека — должны находиться -----------


@pytest.mark.parametrize(
    "query_last,query_first,query_middle",
    [
        ("Магомедов", "Магомед", "Магомедович"),  # точное совпадение
        ("магомедов", "магомед", "магомедович"),  # регистр
        ("Магомёдов", "Магомед", "Магомедович"),  # ё вместо е
        ("Магомедов", "Магомед", "Магомедовиц"),  # опечатка (ч -> ц)
        ("Гъаджиев", "Гаджи", "Гаджиевич"),  # орфографический вариант (гъ/г)
    ],
)
async def test_rosfinmon_finds_spelling_variants(db_session, query_last, query_first, query_middle):
    birth = date(1985, 6, 15)
    # Каноническая запись для "Гъаджиев"-варианта — Гаджиев (тот же человек).
    stored_last = "Гаджиев" if query_last.startswith("Гъ") else "Магомедов"
    stored_first = "Гаджи" if query_last.startswith("Гъ") else "Магомед"
    stored_middle = "Гаджиевич" if query_last.startswith("Гъ") else "Магомедович"
    await _seed_entry(
        db_session, last_name=stored_last, first_name=stored_first, middle_name=stored_middle, birth_date=birth
    )

    source = RosfinmonSource(db_session, name_match_min_similarity=0.7)
    result = await source.check(
        Subject(last_name=query_last, first_name=query_first, middle_name=query_middle, birth_date=birth)
    )

    assert result.status == CheckStatus.HIT
    assert result.parsed["birth_date_matched"] is True
    assert result.parsed["sanctions_list_id"]  # только для отладки/трассировки (id строки на момент проверки)
    assert result.parsed["sanctions_entry_key"]  # T12: устойчивый ключ для подавления ложных срабатываний


# --- разные однофамильцы с разными датами рождения — не должны давать hit --------


@pytest.mark.parametrize(
    "last_name,first_name,middle_name",
    [
        ("Алиев", "Али", "Алиевич"),
        ("Магомедов", "Магомед", "Магомедович"),
        ("Гаджиев", "Гаджи", "Гаджиевич"),
    ],
)
async def test_rosfinmon_same_surname_different_people_no_false_positive(
    db_session, last_name, first_name, middle_name
):
    """Распространённые дагестанские фамилии дают массовые совпадения по ФИО —
    без сверки по дате рождения перечень бесполезен (см. ТЗ)."""
    await _seed_entry(
        db_session, last_name=last_name, first_name=first_name, middle_name=middle_name,
        birth_date=date(1970, 3, 1),
    )
    await _seed_entry(
        db_session, last_name=last_name, first_name=first_name, middle_name=middle_name,
        birth_date=date(1990, 7, 20),
    )

    source = RosfinmonSource(db_session, name_match_min_similarity=0.7)
    result = await source.check(
        Subject(last_name=last_name, first_name=first_name, middle_name=middle_name, birth_date=date(2003, 11, 5))
    )

    assert result.status == CheckStatus.OK


async def test_rosfinmon_exact_name_wrong_birth_date_no_hit(db_session):
    """Полное совпадение ФИО при несовпадении даты рождения — не hit."""
    await _seed_entry(
        db_session, last_name="Гаджиев", first_name="Гаджи", middle_name="Гаджиевич",
        birth_date=date(1985, 6, 15),
    )

    source = RosfinmonSource(db_session, name_match_min_similarity=0.7)
    result = await source.check(
        Subject(last_name="Гаджиев", first_name="Гаджи", middle_name="Гаджиевич", birth_date=date(1999, 1, 1))
    )

    assert result.status == CheckStatus.OK


# --- граница порога -----------------------------------------------------------


async def test_rosfinmon_threshold_boundary(db_session):
    # Опечатка не должна попадать под _VARIANT_SUBSTITUTIONS (ъ/ь и т.п. там
    # вырезаются целиком) — иначе после нормализации строки совпадут буквально,
    # и граница порога окажется недостижима (similarity всегда 1.0).
    birth = date(1985, 6, 15)
    stored_name = normalize_name(full_name("Гаджиев", "Гаджи", "Гаджиевич"))
    query_name = normalize_name(full_name("Гаджиев", "Гаджи", "Гаджиевоч"))  # и -> о

    await _seed_entry(
        db_session, last_name="Гаджиев", first_name="Гаджи", middle_name="Гаджиевич", birth_date=birth
    )

    actual_similarity = (
        await db_session.execute(select(func.similarity(stored_name, query_name)))
    ).scalar_one()
    assert actual_similarity < 1.0  # иначе тест ничего не проверяет

    subject = Subject(last_name="Гаджиев", first_name="Гаджи", middle_name="Гаджиевоч", birth_date=birth)

    source_with_low_threshold = RosfinmonSource(
        db_session, name_match_min_similarity=max(0.0, actual_similarity - 0.05)
    )
    source_with_high_threshold = RosfinmonSource(
        db_session, name_match_min_similarity=min(1.0, actual_similarity + 0.05)
    )

    result_low_threshold = await source_with_low_threshold.check(subject)
    result_high_threshold = await source_with_high_threshold.check(subject)

    assert result_low_threshold.status == CheckStatus.HIT
    assert result_high_threshold.status == CheckStatus.OK


async def test_rosfinmon_no_data_without_name(db_session):
    source = RosfinmonSource(db_session, name_match_min_similarity=0.7)
    result = await source.check(Subject(birth_date=date(1985, 6, 15)))
    assert result.status == CheckStatus.NO_DATA
