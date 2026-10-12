"""T23: демо-данные (app/demo/, app/cli/seed_demo.py).

План и портфель — чистые функции: проверяются диапазоны, доли исходов и что
входные данные заявки действительно ведут к задуманному исходу (ПДН считается
теми же функциями, что у движка). Сквозной тест гонит весь сид на тестовой
базе через настоящий конвейер и fake_partner (её /_test/compose-* и
/partner/webhook — внутри docker-сети, как и в остальных тестах T18)."""

import random
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select

from app.cli import seed_demo
from app.config import get_settings
from app.demo.clock import install_timestamp_listener, uninstall_timestamp_listener, virtual_time
from app.demo.people import SANCTIONS_ONLY_NAMES, PeopleFactory
from app.demo.portfolio import CLOSED_REPOSSESSED, LATE_30_60, LATE_90, build_portfolio
from app.demo.scenarios import (
    GRAY_PTI,
    KIND_COUNTS,
    REJ_A_SANCTIONS,
    REJ_B_DECLARED_PTI,
    REJ_B_LIMITS,
    REJ_B_PTI,
    build_plan,
    demo_summary,
)
from app.engine.bki_features import compute_bki_features, compute_declared_pti_features
from app.engine.passport import is_passport_expired
from app.models import Application, Employee, GoodsCategory

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def plan():
    return build_plan(now=NOW, seed=2026)


def test_plan_is_150_applications_over_60_days_with_realistic_outcome_shares(plan):
    apps = plan.applications
    assert len(apps) == sum(KIND_COUNTS.values()) == 150
    days = Counter(a.submitted_at.date() for a in apps)
    assert len(days) == 60 and max(days.values()) <= 3 and min(days.values()) >= 2
    assert all(NOW - timedelta(days=60) < a.submitted_at < NOW for a in apps)

    shares = {status: count / len(apps) for status, count in demo_summary(plan).items()}
    assert 0.36 <= shares["approved"] <= 0.44
    assert 0.2 <= shares["gray_zone"] <= 0.3
    assert shares["rejected_a"] > 0.05 and shares["rejected_b"] > 0.1 and shares["to_operator"] > 0.03


def test_plan_is_deterministic_by_seed(plan):
    again = build_plan(now=NOW, seed=2026)
    assert [(a.external_id, a.kind, a.applicant.full_name) for a in again.applications] == [
        (a.external_id, a.kind, a.applicant.full_name) for a in plan.applications
    ]
    other = build_plan(now=NOW, seed=7)
    assert [a.applicant.full_name for a in other.applications] != [a.applicant.full_name for a in plan.applications]


def test_plan_ranges_and_uniqueness(plan):
    apps = plan.applications
    for people in ([a.applicant for a in apps] + [a.guarantor for a in apps if a.guarantor],):
        assert len({p.full_name for p in people}) == len(people)
        assert len({(p.passport_series, p.passport_number) for p in people}) == len(people)
        assert len({p.phone for p in people}) == len(people)
    for app in apps:
        person = app.applicant
        assert not is_passport_expired(person.birth_date, person.issue_date, app.submitted_at.date())
        if app.deal is None:
            continue
        assert 15000 <= app.deal.price <= 400000
        assert 3 <= app.deal.term_months <= 24
        assert 30000 <= app.declared_income <= 200000
    categories = {a.deal.goods.category_code for a in apps if a.deal}
    assert {"household_appliances", "electronics", "furniture", "auto_parts", "phones"} <= categories
    assert sum(1 for a in apps if a.guarantor) >= 3


def test_sanctions_names_are_used_only_by_sanctions_hits(plan):
    sanctions_names = {" ".join(n[:3]) for n in SANCTIONS_ONLY_NAMES}
    hits = {a.applicant.full_name for a in plan.applications if a.kind == REJ_A_SANCTIONS}
    assert hits <= sanctions_names
    others = {a.applicant.full_name for a in plan.applications if a.kind != REJ_A_SANCTIONS}
    assert not others & sanctions_names


def test_plan_inputs_lead_to_intended_pti(plan):
    """Тот же расчёт, что у стадии Б: заявленный ПДН до БКИ и ПДН по КИ."""
    for app in plan.applications:
        if app.deal is None or app.kind == REJ_B_LIMITS:
            continue
        declared = compute_declared_pti_features(
            app.declared_income, app.declared_obligations, app.deal.monthly_payment, "partner", pti_max=0.60,
        )["declared_pti"]
        actual = compute_bki_features(
            app.bki_report, monthly_income=app.declared_income,
            declared_monthly_obligations=app.declared_obligations,
            installment_payment=app.deal.monthly_payment, today=app.submitted_at.date(),
        )["features"]["pti"]
        if app.kind == REJ_B_DECLARED_PTI:
            assert declared > 0.60, app.external_id
            continue
        assert declared <= 0.60, app.external_id
        if app.kind == REJ_B_PTI:
            assert actual > 0.60, app.external_id
        elif app.kind == GRAY_PTI:
            assert 0.55 < actual <= 0.60, app.external_id
        else:
            assert actual <= 0.55, app.external_id


def test_people_factory_never_issues_passport_before_last_replacement():
    rng = random.Random(1)
    factory = PeopleFactory(rng, today=date(2026, 10, 12))
    for day in range(400):
        as_of = date(2026, 10, 12) - timedelta(days=day)
        person = factory.make(as_of=as_of)
        assert person.issue_date <= as_of
        assert not is_passport_expired(person.birth_date, person.issue_date, as_of)


def test_portfolio_has_every_delinquency_profile_and_links_to_applications(plan):
    portfolio = build_portfolio(plan, today=NOW.date())
    profiles = Counter(c.profile for c in portfolio.contracts)
    assert 50 <= len(portfolio.contracts) <= 75
    assert profiles[LATE_90] and profiles[LATE_30_60] and profiles[CLOSED_REPOSSESSED]
    demo_refs = {c.application_ref for c in portfolio.contracts if c.application_ref.startswith("DEMO-")}
    approved = {a.external_id for a in plan.applications if a.expected_status == "approved"}
    assert demo_refs <= approved
    for contract in portfolio.contracts:
        assert sum(a for _, _, a in contract.schedule) == contract.total
        assert all(paid_at <= NOW.date() for paid_at, _, _ in contract.payments)
    header = portfolio.contracts_csv.decode("utf-8").splitlines()[0]
    assert header.startswith("Номер договора,Сумма товара")


async def test_virtual_time_stamps_server_default_columns(session_factory):
    moment = datetime(2026, 8, 20, 9, 30, tzinfo=timezone.utc)
    install_timestamp_listener()
    try:
        with virtual_time(moment):
            async with session_factory() as session:
                employee = Employee(full_name="Часы Тестовые", email="clock@example.com", role="viewer",
                                    password_hash="x")
                session.add(employee)
                await session.commit()
                created_at = employee.created_at
    finally:
        uninstall_timestamp_listener()
    assert abs(created_at - moment) < timedelta(seconds=5)

    async with session_factory() as session:
        other = Employee(full_name="Часы Реальные", email="clock2@example.com", role="viewer", password_hash="x")
        session.add(other)
        await session.commit()
        await session.refresh(other)
    assert other.created_at > moment + timedelta(days=30)  # без слушателя — время базы


def test_seed_refuses_in_production():
    settings = get_settings().model_copy(update={"app_env": "production"})
    with pytest.raises(seed_demo.SeedRefused, match="production"):
        seed_demo.check_environment(settings)


def test_seed_refuses_short_password():
    with pytest.raises(seed_demo.SeedRefused, match="12"):
        seed_demo.check_password("short")


async def test_seed_refuses_non_empty_database(session_factory):
    async with session_factory() as session:
        session.add(Application(
            external_id="ANY-1", status="received", goods_name="—", goods_price=Decimal("0"),
            down_payment=Decimal("0"), term_months=0, tariff="unknown", requested_amount=Decimal("0"),
            profile={}, consent_pd_at=NOW, submitted_at=NOW,
        ))
        await session.commit()
    with pytest.raises(seed_demo.SeedRefused, match="docker compose down -v"):
        await seed_demo.check_database_empty(session_factory)


def test_cli_main_refuses_in_production_with_exit_code_1(monkeypatch, capsys):
    monkeypatch.setattr(seed_demo, "get_settings",
                        lambda: get_settings().model_copy(update={"app_env": "production"}))
    monkeypatch.setattr("sys.argv", ["seed_demo", "--password", "long-enough-password"])
    with pytest.raises(SystemExit) as exc:
        seed_demo.main()
    assert exc.value.code == 1
    assert "production" in capsys.readouterr().err


async def test_full_seed_through_real_pipeline(session_factory, tmp_path, partner_admin):
    """Весь сид на тестовой базе: каждая заявка — в задуманном статусе,
    даты размазаны на 60 дней, договоры привязаны, версии правил с авторами."""
    from app.main import app as asgi_app
    from app.models import Contract, Decision, FsspPostDecisionAlert, ManualOverride, RulesetVersion
    from app.storage.local import LocalDirectoryStorage

    settings = get_settings().model_copy(update={"storage_dir": str(tmp_path)})
    previous_storage = asgi_app.state.storage
    asgi_app.state.storage = LocalDirectoryStorage(str(tmp_path))
    now = datetime.now(timezone.utc)
    try:
        plan, portfolio, report = await seed_demo.run_seed(
            password="demo-password-for-tests", seed=2026, fake_partner_url="http://fake_partner:8000",
            session_factory=session_factory, settings=settings, now=now, log=lambda _: None,
        )
        assert report.mismatches == []
        assert [role for role, _ in report.logins] == ["admin", "risk_manager", "compliance", "operator", "viewer"]

        async with session_factory() as session:
            first, last, days = (await session.execute(
                select(func.min(Application.created_at), func.max(Application.created_at),
                       func.count(func.distinct(func.date(Application.created_at))))
                .where(Application.external_id.like("DEMO-%"))
            )).one()
            assert (now - first).days >= 58 and days == 60
            decisions_first = (await session.execute(select(func.min(Decision.created_at)))).scalar_one()
            assert (now - decisions_first).days >= 58  # решения датированы днём заявки, а не днём сида
            versions = (await session.execute(
                select(RulesetVersion.version, RulesetVersion.author_id).order_by(RulesetVersion.version)
            )).all()
            assert len(versions) == 4 and all(author for _, author in versions[1:])
            assert (await session.execute(select(func.count()).select_from(ManualOverride))).scalar_one() == 3
            assert (await session.execute(select(func.count()).select_from(FsspPostDecisionAlert))).scalar_one() == 3
            contracts = (await session.execute(select(func.count(Contract.application_id)))).scalar_one()
            assert contracts == len(portfolio.contracts)
    finally:
        asgi_app.state.storage = previous_storage
        # goods_categories не чистится фикстурой (справочник, см. conftest.REFERENCE_TABLES).
        async with session_factory() as session:
            await session.execute(delete(GoodsCategory).where(GoodsCategory.code.in_(("phones", "auto_parts"))))
            await session.commit()
