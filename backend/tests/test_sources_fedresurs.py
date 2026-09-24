from datetime import date

from app.sources.base import CheckStatus, Subject
from app.sources.fedresurs import FakeFedresursSource, FedresursHttpSource

_SUBJECT = Subject(last_name="Иванов", first_name="Иван", middle_name="Иванович", birth_date=date(1990, 1, 15))


async def test_fake_fedresurs_hit():
    source = FakeFedresursSource(hits={"Иванов|Иван|1990-01-15"})
    result = await source.check(_SUBJECT)
    assert result.status == CheckStatus.HIT
    assert result.parsed == {"bankrupt": True}


async def test_fake_fedresurs_no_hit_is_ok():
    result = await FakeFedresursSource().check(_SUBJECT)
    assert result.status == CheckStatus.OK


async def test_fake_fedresurs_missing_birth_date_is_no_data():
    result = await FakeFedresursSource().check(Subject(last_name="Иванов", first_name="Иван"))
    assert result.status == CheckStatus.NO_DATA


async def test_http_fedresurs_is_always_source_unavailable_stub():
    """T09: внутренний эндпоинт не подтверждён — заглушка, не роняет заявку."""
    result = await FedresursHttpSource().check(_SUBJECT)
    assert result.status == CheckStatus.SOURCE_UNAVAILABLE
    assert result.error_text
