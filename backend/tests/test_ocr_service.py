from datetime import datetime, timezone
from decimal import Decimal

from app.models import Application, OcrResult
from app.ocr.fake_provider import FakeOcrProvider, build_ocr_result
from app.ocr.service import run_ocr


def _make_application(**profile_overrides) -> Application:
    profile = {
        "last_name": "Иванов",
        "first_name": "Иван",
        "middle_name": "Иванович",
        **profile_overrides,
    }
    return Application(
        external_id=f"ext-{id(profile)}",
        status="received",
        goods_name="Холодильник",
        goods_price=Decimal("50000.00"),
        down_payment=Decimal("5000.00"),
        term_months=12,
        tariff="standard",
        requested_amount=Decimal("45000.00"),
        profile=profile,
        consent_pd_at=datetime.now(timezone.utc),
    )


async def test_run_ocr_creates_ocr_result_above_threshold(db_session):
    application = _make_application()
    db_session.add(application)
    await db_session.flush()

    provider = FakeOcrProvider(build_ocr_result(confidence=0.97))
    outcome = await run_ocr(
        db_session, provider, application=application, passport_main=b"main", passport_reg=b"reg"
    )

    assert outcome.below_threshold is False
    assert outcome.name_mismatch is False
    assert outcome.min_confidence == 0.97

    stored = await db_session.get(OcrResult, outcome.ocr_result_id)
    assert stored.provider == "fake"
    assert stored.fields["below_threshold"] is False
    assert stored.fields["fields"]["passport_series"]["value"] == "1234"
    assert float(stored.min_confidence) == 0.97


async def test_run_ocr_below_confidence_threshold(db_session):
    application = _make_application()
    db_session.add(application)
    await db_session.flush()

    provider = FakeOcrProvider(
        build_ocr_result(confidence=0.97, confidence_overrides={"passport_series": 0.5})
    )
    outcome = await run_ocr(
        db_session, provider, application=application, passport_main=b"main", passport_reg=b"reg"
    )

    assert outcome.below_threshold is True
    assert outcome.min_confidence == 0.5


async def test_run_ocr_detects_name_mismatch(db_session):
    application = _make_application(last_name="Петров", first_name="Пётр", middle_name="Петрович")
    db_session.add(application)
    await db_session.flush()

    provider = FakeOcrProvider(build_ocr_result())  # по умолчанию Иванов Иван Иванович
    outcome = await run_ocr(
        db_session, provider, application=application, passport_main=b"main", passport_reg=b"reg"
    )

    assert outcome.name_mismatch is True


async def test_run_ocr_tolerates_typo_in_name(db_session):
    # "Ивнаов" вместо "Иванов" — опечатка, не должна считаться расхождением
    application = _make_application(last_name="Ивнаов", first_name="Иван", middle_name="Иванович")
    db_session.add(application)
    await db_session.flush()

    provider = FakeOcrProvider(build_ocr_result())
    outcome = await run_ocr(
        db_session, provider, application=application, passport_main=b"main", passport_reg=b"reg"
    )

    assert outcome.name_mismatch is False


async def test_run_ocr_sends_only_two_photos_to_provider(db_session):
    application = _make_application()
    db_session.add(application)
    await db_session.flush()

    provider = FakeOcrProvider(build_ocr_result())
    await run_ocr(
        db_session,
        provider,
        application=application,
        passport_main=b"main-bytes",
        passport_reg=b"reg-bytes",
    )

    assert provider.calls == [(b"main-bytes", b"reg-bytes")]


async def test_run_ocr_normalizes_messy_field_values(db_session):
    application = _make_application()
    db_session.add(application)
    await db_session.flush()

    provider = FakeOcrProvider(
        build_ocr_result(
            overrides={
                "passport_series": "12 34",
                "passport_number": "567-890",
                "birth_date": "15.01.1990",
                "department_code": "770 001",
            }
        )
    )
    outcome = await run_ocr(
        db_session, provider, application=application, passport_main=b"main", passport_reg=b"reg"
    )

    assert outcome.fields["passport_series"]["value"] == "1234"
    assert outcome.fields["passport_number"]["value"] == "567890"
    assert outcome.fields["birth_date"]["value"] == "1990-01-15"
    assert outcome.fields["department_code"]["value"] == "770-001"
