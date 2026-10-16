"""T12: комплаенс — снятие заморозки (только эта роль, причина обязательна,
не трогает decisions, заявка того же паспорта проходит стадию А заново) и
разбор ложного совпадения (подавляет будущий hit ТОЛЬКО для той же пары
клиент+запись перечня, ваши уточнения)."""

from datetime import datetime, timezone

from sqlalchemy import select

from app.engine.pipeline import run_stage_a
from app.engine.freeze import find_active_sanctions_freeze
from app.models import Check, Decision, FreezeLift, SanctionsList
from app.ocr.fake_provider import FakeOcrProvider, build_ocr_result
from app.registries.jobs import sync_sanctions_list
from app.sources.name_normalize import full_name, normalize_name
from tests._admin_helpers import create_employee, login
from tests.conftest import unique_valid_passport_series
from tests.test_engine_pipeline import _age_overrides, _ingest, active_ruleset  # noqa: F401

_LAST, _FIRST, _MIDDLE = "Магомедов", "Магомед", "Магомедович"


async def _seed_sanctions_entry(session_factory) -> "SanctionsList":
    async with session_factory() as session:
        entry = SanctionsList(
            list_type="te", full_name=full_name(_LAST, _FIRST, _MIDDLE),
            name_normalized=normalize_name(full_name(_LAST, _FIRST, _MIDDLE)),
            birth_date=None, loaded_at=datetime.now(timezone.utc),
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        return entry


async def _run_matching_applicant(
    partner_admin, partner_client, storage, session_factory, active_ruleset, *, series, age: int = 30
):
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    ocr_provider = FakeOcrProvider(
        build_ocr_result(
            overrides={
                **_age_overrides(age),
                "last_name": _LAST, "first_name": _FIRST, "middle_name": _MIDDLE,
                "passport_series": series, "passport_number": "555555",
            }
        )
    )
    final_status = await run_stage_a(
        session_factory, application_id, ocr_provider=ocr_provider, partner_client=partner_client, storage=storage,
    )
    return application_id, final_status


async def _admin_client(admin_client, session_factory, email="root@example.com"):
    await create_employee(session_factory, email=email, role="admin")
    await login(admin_client, email)
    return admin_client


async def _compliance_client(admin_client, session_factory, email="compliance@example.com"):
    await create_employee(session_factory, email=email, role="admin")
    await login(admin_client, email)
    return admin_client


class _InlineDownloader:
    """Как FixtureDownloader в tests/test_registries_jobs.py, но CSV задаётся
    прямо в тесте — здесь важно точное совпадение содержимого записи с уже
    засеянной (test_false_positive_survives_registry_reload_with_same_entry),
    а не файл фикстуры."""

    def __init__(self, csv_bytes: bytes) -> None:
        self._csv_bytes = csv_bytes

    async def download(self, registry: str) -> bytes:
        return self._csv_bytes


async def test_lift_freeze_requires_reason(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    await _seed_sanctions_entry(session_factory)
    series = unique_valid_passport_series()
    application_id, status_1 = await _run_matching_applicant(
        partner_admin, partner_client, storage, session_factory, active_ruleset, series=series
    )
    assert status_1 == "rejected_a"

    from app.models import Application
    async with session_factory() as session:
        app_row = await session.get(Application, application_id)
        client_id = app_row.client_id
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()

    await _compliance_client(admin_client, session_factory)

    response = await admin_client.post(
        f"/api/compliance/clients/{client_id}/lift-freeze",
        json={"decision_id": str(decision.id), "reason": "   "},
    )
    assert response.status_code == 400


async def test_lift_freeze_does_not_touch_decisions_and_allows_recheck(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    await _seed_sanctions_entry(session_factory)
    series = unique_valid_passport_series()

    application_id_1, status_1 = await _run_matching_applicant(
        partner_admin, partner_client, storage, session_factory, active_ruleset, series=series
    )
    assert status_1 == "rejected_a"

    from app.models import Application
    async with session_factory() as session:
        app_row = await session.get(Application, application_id_1)
        client_id = app_row.client_id
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id_1))
        ).scalar_one()
        decision_snapshot_before = {
            "id": decision.id, "reason_code": decision.reason_code, "freeze": decision.freeze,
            "outcome": decision.outcome, "created_at": decision.created_at,
        }

    # Вторая заявка того же паспорта — заморожена без единой проверки (T09,
    # уже покрыто test_repeat_application_of_frozen_passport_is_rejected_without_any_checks).
    application_id_2, status_2 = await _run_matching_applicant(
        partner_admin, partner_client, storage, session_factory, active_ruleset, series=series
    )
    assert status_2 == "rejected_a"
    async with session_factory() as session:
        checks_2 = (
            await session.execute(select(Check).where(Check.application_id == application_id_2))
        ).scalars().all()
    assert checks_2 == []  # подтверждает, что заморозка ДЕЙСТВИТЕЛЬНО активна

    await _compliance_client(admin_client, session_factory)
    response = await admin_client.post(
        f"/api/compliance/clients/{client_id}/lift-freeze",
        json={"decision_id": str(decision.id), "reason": "клиент подтвердил личность лично"},
    )
    assert response.status_code == 200

    # decisions не менялась — ни одно поле, ни служебные, старая запись как есть.
    async with session_factory() as session:
        decision_after = await session.get(Decision, decision.id)
        assert decision_after.reason_code == decision_snapshot_before["reason_code"]
        assert decision_after.freeze == decision_snapshot_before["freeze"]
        assert decision_after.outcome == decision_snapshot_before["outcome"]
        assert decision_after.created_at == decision_snapshot_before["created_at"]

        lifts = (await session.execute(select(FreezeLift).where(FreezeLift.client_id == client_id))).scalars().all()
        assert len(lifts) == 1
        assert lifts[0].reason == "клиент подтвердил личность лично"

        active_freeze = await find_active_sanctions_freeze(session, client_id=client_id)
    assert active_freeze is None

    # Третья заявка того же паспорта — стадия А проходит ЗАНОВО (проверки
    # реально запускаются, не короткое замыкание на старой заморозке).
    application_id_3, status_3 = await _run_matching_applicant(
        partner_admin, partner_client, storage, session_factory, active_ruleset, series=series
    )
    async with session_factory() as session:
        checks_3 = (
            await session.execute(select(Check).where(Check.application_id == application_id_3))
        ).scalars().all()
    assert {c.source for c in checks_3} == {"mvd", "rosfinmon", "blacklist"}
    # Запись перечня не тронута и не помечена ложной -> тот же hit снова
    # замораживает — это ОЖИДАЕМО (снятие относилось к прошлому решению, а не
    # к самой записи перечня).
    assert status_3 == "rejected_a"


async def test_mark_false_positive_suppresses_only_same_client_and_entry(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    entry = await _seed_sanctions_entry(session_factory)
    series = unique_valid_passport_series()

    application_id_1, status_1 = await _run_matching_applicant(
        partner_admin, partner_client, storage, session_factory, active_ruleset, series=series
    )
    assert status_1 == "rejected_a"

    from app.models import Application
    async with session_factory() as session:
        app_row = await session.get(Application, application_id_1)
        client_id = app_row.client_id
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id_1))
        ).scalar_one()

    await _compliance_client(admin_client, session_factory)
    lift_response = await admin_client.post(
        f"/api/compliance/clients/{client_id}/lift-freeze",
        json={"decision_id": str(decision.id), "reason": "разбор показал совпадение с другим человеком"},
    )
    assert lift_response.status_code == 200

    mark_response = await admin_client.post(
        f"/api/compliance/decisions/{decision.id}/mark-false-positive",
        json={"verdict": "false_positive", "comment": "не тот человек"},
    )
    assert mark_response.status_code == 200

    # Повторная заявка ТОГО ЖЕ клиента — hit подавлен, только информационная
    # пометка в Check.parsed, сырой факт (status=hit) не переписан.
    application_id_2, status_2 = await _run_matching_applicant(
        partner_admin, partner_client, storage, session_factory, active_ruleset, series=series
    )
    async with session_factory() as session:
        rosfinmon_check = (
            await session.execute(
                select(Check).where(Check.application_id == application_id_2, Check.source == "rosfinmon")
            )
        ).scalar_one()
    assert rosfinmon_check.status == "hit"  # сырой Check не переписан (правило 1/3 CLAUDE.md)
    assert status_2 != "rejected_a"  # но решение больше не блокируется этим совпадением

    async with session_factory() as session:
        decision_2 = (
            await session.execute(select(Decision).where(Decision.application_id == application_id_2))
        ).scalar_one()
    assert decision_2.reason_code != "SANCTIONS_HIT"
    assert decision_2.freeze is False


async def test_mark_false_positive_does_not_suppress_different_client(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Если запись перечня всё та же, но клиент — другой (другой паспорт,
    другое ФИО+ДР), совпадение срабатывает как обычно (ваше уточнение)."""
    await _seed_sanctions_entry(session_factory)
    series_1 = unique_valid_passport_series()
    series_2 = unique_valid_passport_series()

    application_id_1, status_1 = await _run_matching_applicant(
        partner_admin, partner_client, storage, session_factory, active_ruleset, series=series_1
    )
    assert status_1 == "rejected_a"

    from app.models import Application
    async with session_factory() as session:
        app_row = await session.get(Application, application_id_1)
        client_id_1 = app_row.client_id
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id_1))
        ).scalar_one()

    await _compliance_client(admin_client, session_factory)
    await admin_client.post(
        f"/api/compliance/clients/{client_id_1}/lift-freeze",
        json={"decision_id": str(decision.id), "reason": "разбор"},
    )
    await admin_client.post(
        f"/api/compliance/decisions/{decision.id}/mark-false-positive",
        json={"verdict": "false_positive"},
    )

    # Другой человек с ТЕМ ЖЕ ФИО, но другого возраста (другая дата рождения) и
    # другим паспортом — find_or_create_client (ФИО+ДР) не объединит его с
    # первым клиентом, это генуинно другой человек с распространённой фамилией.
    application_id_2, status_2 = await _run_matching_applicant(
        partner_admin, partner_client, storage, session_factory, active_ruleset, series=series_2, age=45,
    )
    async with session_factory() as session:
        app_row_2 = await session.get(Application, application_id_2)
    assert app_row_2.client_id != client_id_1  # действительно другой клиент
    assert status_2 == "rejected_a"  # ложное для ДРУГОГО клиента не подавляет этот hit


async def test_false_positive_survives_registry_reload_with_same_entry(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset
):
    """Реестр перезаливается целиком (app/registries/sync.py: rename+DROP) —
    id строки меняется, даже если содержимое записи не менялось. Пометка
    «ложное совпадение» не должна слетать в этом случае, иначе однофамилец
    снова получает отказ и заморозку на следующий день (ваше уточнение)."""
    entry = await _seed_sanctions_entry(session_factory)
    series = unique_valid_passport_series()

    application_id_1, status_1 = await _run_matching_applicant(
        partner_admin, partner_client, storage, session_factory, active_ruleset, series=series
    )
    assert status_1 == "rejected_a"

    from app.models import Application
    async with session_factory() as session:
        app_row = await session.get(Application, application_id_1)
        client_id = app_row.client_id
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id_1))
        ).scalar_one()

    await _compliance_client(admin_client, session_factory)
    await admin_client.post(
        f"/api/compliance/clients/{client_id}/lift-freeze",
        json={"decision_id": str(decision.id), "reason": "разбор показал совпадение с другим человеком"},
    )
    mark_response = await admin_client.post(
        f"/api/compliance/decisions/{decision.id}/mark-false-positive",
        json={"verdict": "false_positive", "comment": "не тот человек"},
    )
    assert mark_response.status_code == 200

    # Перезаливка реестра ТОЙ ЖЕ записью (то же ФИО, та же дата рождения) —
    # настоящий sync_sanctions_list, не подделка id вручную.
    csv_content = f"list_type,full_name,birth_date\nte,{full_name(_LAST, _FIRST, _MIDDLE)},\n".encode("utf-8")
    log = await sync_sanctions_list(session_factory, _InlineDownloader(csv_content))
    assert log.status == "ok"

    async with session_factory() as session:
        reloaded_entry = (
            await session.execute(
                select(SanctionsList).where(SanctionsList.full_name == full_name(_LAST, _FIRST, _MIDDLE))
            )
        ).scalar_one()
    assert reloaded_entry.id != entry.id  # реально новая строка, не апдейт на месте

    # Повторная заявка того же клиента — hit по-прежнему подавлен, несмотря на
    # смену id записи перечня.
    application_id_2, status_2 = await _run_matching_applicant(
        partner_admin, partner_client, storage, session_factory, active_ruleset, series=series
    )
    assert status_2 != "rejected_a"

    async with session_factory() as session:
        decision_2 = (
            await session.execute(select(Decision).where(Decision.application_id == application_id_2))
        ).scalar_one()
    assert decision_2.reason_code != "SANCTIONS_HIT"
    assert decision_2.freeze is False


async def test_compliance_lookup_failure_fails_closed_to_sanctions_reject(
    partner_admin, partner_client, storage, session_factory, active_ruleset, monkeypatch
):
    """При сбое поиска пометки ложного совпадения — отказ, не подавление (ваше
    уточнение): санкционная проверка ведёт себя как обычный hit, даже если бы
    пометка false_positive формально существовала для этой пары."""
    await _seed_sanctions_entry(session_factory)
    series = unique_valid_passport_series()

    async def _broken_fetch(*args, **kwargs):
        raise RuntimeError("симулированный сбой БД")

    monkeypatch.setattr("app.engine.compliance_overrides._fetch_latest_verdict", _broken_fetch)

    application_id, final_status = await _run_matching_applicant(
        partner_admin, partner_client, storage, session_factory, active_ruleset, series=series
    )
    assert final_status == "rejected_a"

    async with session_factory() as session:
        decision = (
            await session.execute(select(Decision).where(Decision.application_id == application_id))
        ).scalar_one()
    assert decision.reason_code == "SANCTIONS_HIT"
    assert decision.freeze is True
