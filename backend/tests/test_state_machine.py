from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.engine.state_machine import InvalidTransitionError, transition
from app.models import Application, StatusHistory


def _make_application(status: str = "received") -> Application:
    return Application(
        external_id=f"ext-{id(object())}",
        status=status,
        goods_name="Холодильник",
        goods_price=Decimal("50000.00"),
        down_payment=Decimal("5000.00"),
        term_months=12,
        tariff="standard",
        requested_amount=Decimal("45000.00"),
        profile={},
        consent_pd_at=datetime.now(timezone.utc),
    )


async def test_valid_transition_updates_status_and_writes_history(db_session):
    application = _make_application("received")
    db_session.add(application)
    await db_session.flush()

    transition(db_session, application, "ocr", actor_type="system")
    await db_session.flush()

    assert application.status == "ocr"

    history = (
        await db_session.execute(
            select(StatusHistory).where(StatusHistory.application_id == application.id)
        )
    ).scalar_one()
    assert history.from_status == "received"
    assert history.to_status == "ocr"
    assert history.actor_type == "system"


async def test_invalid_transition_raises_and_does_not_change_status(db_session):
    application = _make_application("received")
    db_session.add(application)
    await db_session.flush()

    with pytest.raises(InvalidTransitionError):
        transition(db_session, application, "approved", actor_type="system")

    assert application.status == "received"


@pytest.mark.parametrize(
    "from_status,to_status",
    [
        ("received", "ocr"),
        ("ocr", "ocr_failed"),
        ("ocr", "stage_a"),
        ("ocr_failed", "to_operator"),
        ("stage_a", "rejected_a"),
        ("stage_a", "to_operator"),
        ("to_operator", "stage_b"),
        ("stage_b", "rejected_b"),
        ("stage_b", "gray_zone"),
        ("stage_b", "approved"),
        ("stage_b", "to_operator"),
        ("gray_zone", "stage_b"),
        ("approved", "contracted"),
        ("approved", "expired"),
    ],
)
async def test_all_diagram_transitions_allowed(db_session, from_status, to_status):
    application = _make_application(from_status)
    db_session.add(application)
    await db_session.flush()

    transition(db_session, application, to_status, actor_type="system")
    assert application.status == to_status


@pytest.mark.parametrize(
    "from_status,to_status",
    [
        ("rejected_a", "to_operator"),
        ("rejected_b", "gray_zone"),
        ("contracted", "expired"),
        ("expired", "contracted"),
        ("received", "stage_a"),  # нельзя перескочить OCR
        ("to_operator", "rejected_a"),  # решение стадии А уже нельзя переиграть отсюда
    ],
)
async def test_disallowed_transitions_raise(db_session, from_status, to_status):
    application = _make_application(from_status)
    db_session.add(application)
    await db_session.flush()

    with pytest.raises(InvalidTransitionError):
        transition(db_session, application, to_status, actor_type="system")
