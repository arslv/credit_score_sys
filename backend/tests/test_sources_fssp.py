from datetime import date

import httpx
import pytest
import respx
from httpx import Response

from app.sources.base import CheckStatus, Subject
from app.sources.fssp import FakeFsspSource, FsspHttpSource, FsspSubmitError

_SUBJECT = Subject(last_name="Иванов", first_name="Иван", middle_name="Иванович", birth_date=date(1990, 1, 15))


# --- FakeFsspSource -----------------------------------------------------------


async def test_fake_submit_missing_birth_date_raises():
    with pytest.raises(FsspSubmitError):
        await FakeFsspSource().submit(Subject(last_name="Иванов", first_name="Иван"))


async def test_fake_submit_can_be_forced_to_fail():
    with pytest.raises(FsspSubmitError):
        await FakeFsspSource(raise_on_submit=True).submit(_SUBJECT)


async def test_fake_poll_not_ready_then_ready():
    source = FakeFsspSource(
        productions_by_request={"fake-1": [{"subject": "Штраф ГИБДД", "amount": "3000"}]},
        not_ready_polls=2,
    )
    request_id = await source.submit(_SUBJECT)

    assert await source.poll(request_id) is None
    assert await source.poll(request_id) is None
    result = await source.poll(request_id)

    assert result is not None
    assert result.status == CheckStatus.HIT
    assert result.parsed["productions"] == [{"subject": "Штраф ГИБДД", "amount": "3000"}]


async def test_fake_poll_never_ready():
    source = FakeFsspSource(never_ready=True)
    request_id = await source.submit(_SUBJECT)
    assert await source.poll(request_id) is None
    assert await source.poll(request_id) is None


async def test_fake_poll_empty_productions_is_ok():
    source = FakeFsspSource()
    request_id = await source.submit(_SUBJECT)
    result = await source.poll(request_id)
    assert result.status == CheckStatus.OK
    assert result.parsed == {"productions": []}


# --- FsspHttpSource -------------------------------------------------------------


@respx.mock
async def test_http_submit_success():
    respx.post("https://fssp.example/api/v1/requests").mock(
        return_value=Response(200, json={"request_id": "abc123"})
    )
    source = FsspHttpSource(
        base_url="https://fssp.example", api_key="k", submit_timeout_seconds=5, submit_max_attempts=1
    )

    request_id = await source.submit(_SUBJECT)

    assert request_id == "abc123"


@respx.mock
async def test_http_submit_5xx_raises_after_retries():
    route = respx.post("https://fssp.example/api/v1/requests").mock(return_value=Response(500))
    source = FsspHttpSource(
        base_url="https://fssp.example", api_key="k", submit_timeout_seconds=5, submit_max_attempts=2
    )

    with pytest.raises(FsspSubmitError):
        await source.submit(_SUBJECT)
    assert route.call_count == 2


@respx.mock
async def test_http_submit_timeout_raises():
    respx.post("https://fssp.example/api/v1/requests").mock(side_effect=httpx.TimeoutException("timeout"))
    source = FsspHttpSource(
        base_url="https://fssp.example", api_key="k", submit_timeout_seconds=5, submit_max_attempts=1
    )

    with pytest.raises(FsspSubmitError):
        await source.submit(_SUBJECT)


async def test_http_submit_missing_data_raises_without_network_call():
    source = FsspHttpSource(
        base_url="https://fssp.example", api_key="k", submit_timeout_seconds=5, submit_max_attempts=1
    )
    with pytest.raises(FsspSubmitError):
        await source.submit(Subject(last_name="Иванов"))


@respx.mock
async def test_http_poll_not_ready_is_none():
    respx.get("https://fssp.example/api/v1/requests/abc123").mock(
        return_value=Response(200, json={"status": "pending"})
    )
    source = FsspHttpSource(
        base_url="https://fssp.example", api_key="k", submit_timeout_seconds=5, submit_max_attempts=1
    )

    assert await source.poll("abc123") is None


@respx.mock
async def test_http_poll_ready_with_productions_is_hit():
    respx.get("https://fssp.example/api/v1/requests/abc123").mock(
        return_value=Response(
            200, json={"status": "ready", "productions": [{"subject": "Кредит", "amount": "100000"}]}
        )
    )
    source = FsspHttpSource(
        base_url="https://fssp.example", api_key="k", submit_timeout_seconds=5, submit_max_attempts=1
    )

    result = await source.poll("abc123")

    assert result.status == CheckStatus.HIT
    assert result.parsed["productions"] == [{"subject": "Кредит", "amount": "100000"}]


@respx.mock
async def test_http_poll_ready_no_productions_is_ok():
    respx.get("https://fssp.example/api/v1/requests/abc123").mock(
        return_value=Response(200, json={"status": "ready", "productions": []})
    )
    source = FsspHttpSource(
        base_url="https://fssp.example", api_key="k", submit_timeout_seconds=5, submit_max_attempts=1
    )

    result = await source.poll("abc123")
    assert result.status == CheckStatus.OK


@respx.mock
async def test_http_poll_network_error_is_source_unavailable_not_raised():
    respx.get("https://fssp.example/api/v1/requests/abc123").mock(side_effect=httpx.TimeoutException("timeout"))
    source = FsspHttpSource(
        base_url="https://fssp.example", api_key="k", submit_timeout_seconds=5, submit_max_attempts=1
    )

    result = await source.poll("abc123")

    assert result.status == CheckStatus.SOURCE_UNAVAILABLE
