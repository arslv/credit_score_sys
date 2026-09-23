from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.models import Check, Client, Decision, FeatureSnapshot
from app.models.application import Application
from app.models.ruleset import RulesetVersion


def _now():
    return datetime.now(timezone.utc)


async def _make_application(session, **overrides):
    application = Application(
        external_id=overrides.pop("external_id", "ext-1"),
        status=overrides.pop("status", "received"),
        goods_name="Холодильник",
        goods_price=Decimal("50000.00"),
        down_payment=Decimal("5000.00"),
        term_months=12,
        tariff="standard",
        requested_amount=Decimal("45000.00"),
        profile={"phone": "+79990000000"},
        consent_pd_at=_now(),
        **overrides,
    )
    session.add(application)
    await session.flush()
    return application


async def test_client_application_check_decision_chain(db_session):
    client = Client(
        last_name="Иванов",
        first_name="Иван",
        birth_date=date(1990, 1, 1),
        passport_series="1234",
        passport_number="567890",
        phone="+79990000000",
    )
    db_session.add(client)
    await db_session.flush()

    application = await _make_application(db_session, client_id=client.id)

    check = Check(
        application_id=application.id,
        subject_type="applicant",
        source="mvd",
        status="ok",
        response_raw={"result": "clean"},
        started_at=_now(),
    )
    db_session.add(check)

    ruleset = RulesetVersion(
        version=1,
        config={"hard_stops": []},
        config_hash="0" * 64,
        effective_from=_now(),
    )
    db_session.add(ruleset)
    await db_session.flush()

    snapshot = FeatureSnapshot(
        application_id=application.id,
        stage="A",
        features={"age": 35},
        feature_set_ver="v1",
    )
    db_session.add(snapshot)
    await db_session.flush()

    decision = Decision(
        application_id=application.id,
        stage="A",
        outcome="manual_review",
        triggered_rules=[],
        ruleset_version_id=ruleset.id,
        feature_snapshot_id=snapshot.id,
    )
    db_session.add(decision)
    await db_session.flush()

    stored = await db_session.get(Decision, decision.id)
    assert stored is not None
    assert stored.application_id == application.id
    assert stored.ruleset_version_id == ruleset.id
    assert stored.feature_snapshot_id == snapshot.id


async def test_application_without_client_is_allowed(db_session):
    application = await _make_application(db_session, external_id="ext-no-client")

    assert application.client_id is None


async def test_numeric_fields_round_trip_as_decimal(db_session):
    application = await _make_application(db_session, external_id="ext-decimal")

    result = await db_session.execute(
        select(Application).where(Application.id == application.id)
    )
    stored = result.scalar_one()

    assert isinstance(stored.goods_price, Decimal)
    assert stored.goods_price == Decimal("50000.00")
    assert isinstance(stored.down_payment, Decimal)
    assert isinstance(stored.requested_amount, Decimal)
