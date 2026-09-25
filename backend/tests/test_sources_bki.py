"""T11: BkiProvider — интерфейс, заглушка, сериализация отчёта для кэша."""

from datetime import date

from app.sources.base import CheckStatus, Subject
from app.sources.bki import (
    BkiReport,
    FakeBkiProvider,
    NotConfiguredBkiProvider,
    deserialize_report,
    empty_history_report,
    good_history_report,
    overdue_history_report,
    overloaded_report,
    serialize_report,
)

_SUBJECT = Subject(last_name="Иванов", first_name="Иван", birth_date=date(1990, 5, 20))


async def test_not_configured_provider_never_calls_bureau():
    provider = NotConfiguredBkiProvider()
    assert provider.is_configured is False

    result = await provider.request_report(_SUBJECT)
    assert result.status is CheckStatus.SOURCE_UNAVAILABLE
    assert result.report is None


async def test_fake_provider_no_record_returns_no_data():
    provider = FakeBkiProvider()
    assert provider.is_configured is True

    result = await provider.request_report(_SUBJECT)
    assert result.status is CheckStatus.NO_DATA
    assert result.report == BkiReport()


async def test_fake_provider_returns_configured_report_by_identity():
    key = f"{_SUBJECT.last_name}|{_SUBJECT.first_name}|{_SUBJECT.birth_date.isoformat()}"
    provider = FakeBkiProvider(reports={key: good_history_report()})

    result = await provider.request_report(_SUBJECT)
    assert result.status is CheckStatus.OK
    assert result.report.score == 780
    assert provider.requested_subjects == [_SUBJECT]


async def test_fake_provider_can_simulate_unavailable_and_error():
    unavailable = FakeBkiProvider(raise_unavailable=True)
    result = await unavailable.request_report(_SUBJECT)
    assert result.status is CheckStatus.SOURCE_UNAVAILABLE

    erroring = FakeBkiProvider(raise_error=True)
    result = await erroring.request_report(_SUBJECT)
    assert result.status is CheckStatus.ERROR


def test_synthetic_scenarios_are_distinct():
    """Критерий приёмки T11: хорошая история, просрочки, перегруз, пустая история."""
    good = good_history_report()
    overdue = overdue_history_report()
    overloaded = overloaded_report()
    empty = empty_history_report()

    assert good.score is not None and good.score >= 700
    assert all(c.overdue_days_max == 0 for c in good.contracts)

    assert any(c.overdue_days_max >= 90 for c in overdue.contracts)
    assert overdue.score is not None and overdue.score < 450

    assert len(overloaded.contracts) >= 3
    assert len(overloaded.inquiries) >= 3

    assert empty.contracts == []
    assert empty.inquiries == []
    assert empty.score is None


def test_serialize_deserialize_round_trip():
    report = overloaded_report()
    raw = serialize_report(report)
    restored = deserialize_report(raw)

    assert restored == report


def test_deserialize_empty_raw_is_empty_report():
    assert deserialize_report({}) == BkiReport()
    assert deserialize_report(None) == BkiReport()


def test_serialize_report_dates_are_json_safe_strings():
    raw = serialize_report(good_history_report())
    contract = raw["contracts"][0]
    assert isinstance(contract["opened_at"], str)
    assert isinstance(contract["closed_at"], str)
    inquiry = raw["inquiries"][0]
    assert isinstance(inquiry["requested_at"], str)
