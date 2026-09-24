from datetime import datetime, timezone
from decimal import Decimal

from app.engine.duplicates import is_passport_reused_with_different_identity
from app.models import Application, OcrResult


async def _make_application(session, external_id: str) -> Application:
    application = Application(
        external_id=external_id,
        status="stage_a",
        goods_name="Холодильник",
        goods_price=Decimal("50000.00"),
        down_payment=Decimal("5000.00"),
        term_months=12,
        tariff="standard",
        requested_amount=Decimal("45000.00"),
        profile={},
        consent_pd_at=datetime.now(timezone.utc),
    )
    session.add(application)
    await session.flush()
    return application


def _ocr_fields(*, last_name: str, first_name: str, middle_name: str | None, birth_date: str) -> dict:
    def field(value):
        return {"value": value, "confidence": 0.98}

    return {
        "fields": {
            "last_name": field(last_name),
            "first_name": field(first_name),
            "middle_name": field(middle_name),
            "birth_date": field(birth_date),
            "passport_series": field("1234"),
            "passport_number": field("567890"),
        },
        "flags": {},
        "below_threshold": False,
    }


async def _add_ocr_result(session, application: Application, **kwargs) -> None:
    session.add(
        OcrResult(
            application_id=application.id,
            provider="fake",
            response_raw={},
            fields=_ocr_fields(**kwargs),
            min_confidence=Decimal("0.980"),
        )
    )
    await session.flush()


async def test_no_prior_applications_means_not_reused(db_session):
    application = await _make_application(db_session, "ext-1")

    result = await is_passport_reused_with_different_identity(
        db_session,
        application_id=application.id,
        passport_series="1234",
        passport_number="567890",
        last_name="Иванов",
        first_name="Иван",
        middle_name="Иванович",
        birth_date="1990-01-15",
    )

    assert result is False


async def test_same_passport_same_identity_is_not_reused(db_session):
    other = await _make_application(db_session, "ext-other")
    await _add_ocr_result(
        db_session, other,
        last_name="Иванов", first_name="Иван", middle_name="Иванович", birth_date="1990-01-15",
    )

    this = await _make_application(db_session, "ext-this")
    result = await is_passport_reused_with_different_identity(
        db_session,
        application_id=this.id,
        passport_series="1234",
        passport_number="567890",
        last_name="Иванов",
        first_name="Иван",
        middle_name="Иванович",
        birth_date="1990-01-15",
    )

    assert result is False


async def test_same_passport_different_name_is_reused(db_session):
    other = await _make_application(db_session, "ext-other")
    await _add_ocr_result(
        db_session, other,
        last_name="Иванов", first_name="Иван", middle_name="Иванович", birth_date="1990-01-15",
    )

    this = await _make_application(db_session, "ext-this")
    result = await is_passport_reused_with_different_identity(
        db_session,
        application_id=this.id,
        passport_series="1234",
        passport_number="567890",
        last_name="Петров",
        first_name="Пётр",
        middle_name="Петрович",
        birth_date="1990-01-15",
    )

    assert result is True


async def test_same_passport_different_birth_date_is_reused(db_session):
    other = await _make_application(db_session, "ext-other")
    await _add_ocr_result(
        db_session, other,
        last_name="Иванов", first_name="Иван", middle_name="Иванович", birth_date="1990-01-15",
    )

    this = await _make_application(db_session, "ext-this")
    result = await is_passport_reused_with_different_identity(
        db_session,
        application_id=this.id,
        passport_series="1234",
        passport_number="567890",
        last_name="Иванов",
        first_name="Иван",
        middle_name="Иванович",
        birth_date="1995-05-20",
    )

    assert result is True


async def test_same_passport_typo_in_name_is_not_reused(db_session):
    """Нечёткое сравнение через normalize_name — опечатка не считается сменой личности."""
    other = await _make_application(db_session, "ext-other")
    await _add_ocr_result(
        db_session, other,
        last_name="Магомедов", first_name="Магомед", middle_name="Магомедович", birth_date="1990-01-15",
    )

    this = await _make_application(db_session, "ext-this")
    result = await is_passport_reused_with_different_identity(
        db_session,
        application_id=this.id,
        passport_series="1234",
        passport_number="567890",
        last_name="магомедов",  # регистр
        first_name="Магомед",
        middle_name="Магомедович",
        birth_date="1990-01-15",
    )

    assert result is False


async def test_only_latest_ocr_result_per_application_is_compared(session_factory):
    """Если у ДРУГОЙ заявки было два прогона OCR (опечатка исправлена оператором),
    сравниваем с последним, а не с первым. Каждая вставка — в своей транзакции
    (session_factory, а не db_session): created_at в Postgres — время НАЧАЛА
    транзакции, а не момента INSERT, и обе вставки в одной транзакции получили бы
    одинаковый created_at, из-за чего "последняя" была бы не определена."""
    async with session_factory() as session:
        other = await _make_application(session, "ext-only-latest-other")
        await session.commit()

    async with session_factory() as session:
        other = await session.get(Application, other.id)
        await _add_ocr_result(
            session, other,
            last_name="Опечаткин", first_name="Иван", middle_name="Иванович", birth_date="1990-01-15",
        )
        await session.commit()

    async with session_factory() as session:
        other = await session.get(Application, other.id)
        await _add_ocr_result(
            session, other,
            last_name="Иванов", first_name="Иван", middle_name="Иванович", birth_date="1990-01-15",
        )
        await session.commit()

    async with session_factory() as session:
        this = await _make_application(session, "ext-only-latest-this")
        await session.commit()

        result = await is_passport_reused_with_different_identity(
            session,
            application_id=this.id,
            passport_series="1234",
            passport_number="567890",
            last_name="Иванов",
            first_name="Иван",
            middle_name="Иванович",
            birth_date="1990-01-15",
        )

    assert result is False


async def test_different_passport_number_is_not_compared(db_session):
    other = await _make_application(db_session, "ext-other")
    other_fields = _ocr_fields(
        last_name="Совсем", first_name="Другой", middle_name=None, birth_date="1970-01-01"
    )
    other_fields["fields"]["passport_number"] = {"value": "111111", "confidence": 0.98}
    db_session.add(
        OcrResult(
            application_id=other.id,
            provider="fake",
            response_raw={},
            fields=other_fields,
            min_confidence=Decimal("0.980"),
        )
    )
    await db_session.flush()

    this = await _make_application(db_session, "ext-this")
    result = await is_passport_reused_with_different_identity(
        db_session,
        application_id=this.id,
        passport_series="1234",
        passport_number="567890",
        last_name="Иванов",
        first_name="Иван",
        middle_name="Иванович",
        birth_date="1990-01-15",
    )

    assert result is False
