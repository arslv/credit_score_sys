from datetime import date

import httpx
import respx
from httpx import Response

from app.sources.base import CheckStatus, Subject
from app.sources.fns import FakeFnsSource, FnsHttpSource

_SUBJECT = Subject(last_name="Иванов", first_name="Иван", middle_name="Иванович", birth_date=date(1990, 1, 15))


async def test_fake_fns_returns_configured_inn():
    source = FakeFnsSource(responses={"Иванов|Иван|1990-01-15": "770123456789"})
    result = await source.check(_SUBJECT)
    assert result.status == CheckStatus.OK
    assert result.parsed == {"inn": "770123456789"}


async def test_fake_fns_missing_birth_date_is_no_data():
    result = await FakeFnsSource().check(Subject(last_name="Иванов", first_name="Иван"))
    assert result.status == CheckStatus.NO_DATA


@respx.mock
async def test_http_fns_success():
    respx.post("https://fns.example/").mock(return_value=Response(200, json={"inn": "770123456789"}))
    source = FnsHttpSource(base_url="https://fns.example", timeout_seconds=5, max_attempts=1)

    result = await source.check(_SUBJECT)

    assert result.status == CheckStatus.OK
    assert result.parsed == {"inn": "770123456789"}


@respx.mock
async def test_http_fns_empty_response_is_no_data():
    respx.post("https://fns.example/").mock(return_value=Response(200, json={}))
    source = FnsHttpSource(base_url="https://fns.example", timeout_seconds=5, max_attempts=1)

    result = await source.check(_SUBJECT)

    assert result.status == CheckStatus.NO_DATA


@respx.mock
async def test_http_fns_5xx_is_source_unavailable_after_retries():
    route = respx.post("https://fns.example/").mock(return_value=Response(500))
    source = FnsHttpSource(base_url="https://fns.example", timeout_seconds=5, max_attempts=2)

    result = await source.check(_SUBJECT)

    assert result.status == CheckStatus.SOURCE_UNAVAILABLE
    assert route.call_count == 2  # исчерпаны все попытки


@respx.mock
async def test_http_fns_timeout_is_source_unavailable():
    respx.post("https://fns.example/").mock(side_effect=httpx.TimeoutException("timeout"))
    source = FnsHttpSource(base_url="https://fns.example", timeout_seconds=5, max_attempts=1)

    result = await source.check(_SUBJECT)

    assert result.status == CheckStatus.SOURCE_UNAVAILABLE


async def test_http_fns_missing_required_fields_skips_network_call():
    source = FnsHttpSource(base_url="https://fns.example", timeout_seconds=5, max_attempts=1)
    result = await source.check(Subject(last_name="Иванов"))
    assert result.status == CheckStatus.NO_DATA
