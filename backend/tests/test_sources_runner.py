from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.models import Application, Check
from app.sources.base import CheckResult, CheckStatus, Subject
from app.sources.runner import run_and_record_check


class RaisingSource:
    source_name = "mvd"

    async def check(self, subject: Subject) -> CheckResult:
        raise RuntimeError("источник упал")


class GarbageSource:
    source_name = "rosfinmon"

    async def check(self, subject: Subject):
        return {"status": "hit"}  # не CheckResult — нарушение контракта


class OkSource:
    source_name = "blacklist"

    async def check(self, subject: Subject) -> CheckResult:
        return CheckResult(status=CheckStatus.OK)


async def _make_application(session) -> Application:
    application = Application(
        external_id=f"ext-{id(object())}",
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


async def test_source_exception_does_not_propagate_and_is_recorded(db_session):
    application = await _make_application(db_session)

    check = await run_and_record_check(
        db_session,
        RaisingSource(),
        application_id=application.id,
        subject_type="applicant",
        subject_id=None,
        subject=Subject(),
    )

    assert check.status == "error"
    assert "упал" in check.error_text

    stored = (
        await db_session.execute(select(Check).where(Check.application_id == application.id))
    ).scalar_one()
    assert stored.status == "error"
    assert stored.source == "mvd"


async def test_source_returning_garbage_is_treated_as_error(db_session):
    application = await _make_application(db_session)

    check = await run_and_record_check(
        db_session,
        GarbageSource(),
        application_id=application.id,
        subject_type="applicant",
        subject_id=None,
        subject=Subject(),
    )

    assert check.status == "error"
    assert "CheckResult" in check.error_text

    stored = (
        await db_session.execute(select(Check).where(Check.application_id == application.id))
    ).scalar_one()
    assert stored.status == "error"


async def test_pipeline_continues_after_source_failure(db_session):
    """Одна проверка упала — остальные всё равно выполняются и пишутся."""
    application = await _make_application(db_session)

    failing_check = await run_and_record_check(
        db_session,
        RaisingSource(),
        application_id=application.id,
        subject_type="applicant",
        subject_id=None,
        subject=Subject(),
    )
    ok_check = await run_and_record_check(
        db_session,
        OkSource(),
        application_id=application.id,
        subject_type="applicant",
        subject_id=None,
        subject=Subject(),
    )

    assert failing_check.status == "error"
    assert ok_check.status == "ok"

    all_checks = (
        await db_session.execute(select(Check).where(Check.application_id == application.id))
    ).scalars().all()
    assert {c.source for c in all_checks} == {"mvd", "blacklist"}
