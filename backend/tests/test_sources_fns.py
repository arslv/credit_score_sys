from datetime import date

import httpx
import respx
from httpx import Response

from app.redis import get_redis
from app.sources.base import CheckStatus, Subject
from app.sources.fns import (
    FakeFnsEgripSource,
    FakeFnsInnSource,
    FakeFnsNpdSource,
    FnsEgripHttpSource,
    FnsInnHttpSource,
    FnsNpdHttpSource,
)

_SUBJECT = Subject(
    last_name="Иванов", first_name="Иван", middle_name="Иванович", birth_date=date(1990, 1, 15),
    passport_series="4501", passport_number="123456", passport_issue_date=date(2010, 5, 20),
)


# --- fake-источники (для сборки без ключей/сети, e2e_check.py и т.п.) ---

async def test_fake_fns_inn_returns_configured_inn():
    source = FakeFnsInnSource(responses={"Иванов|Иван|1990-01-15": "770123456789"})
    result = await source.check(_SUBJECT)
    assert result.status == CheckStatus.OK
    assert result.parsed["inn"] == "770123456789"


async def test_fake_fns_inn_missing_required_field_is_error_not_no_data():
    """T16, живой прогон (ваше явное указание, п.2/3 правок): пустой
    обязательный Subject — ERROR с понятной причиной, не NO_DATA (NO_DATA —
    «искали и не нашли», это — «даже не искали, нечем»)."""
    result = await FakeFnsInnSource().check(Subject(last_name="Иванов", first_name="Иван"))
    assert result.status == CheckStatus.ERROR
    assert "дата рождения" in result.error_text


async def test_fake_fns_inn_unknown_is_no_data():
    result = await FakeFnsInnSource().check(_SUBJECT)
    assert result.status == CheckStatus.NO_DATA


async def test_fake_fns_npd_self_employed():
    result = await FakeFnsNpdSource(responses={"770123456789": True}).check_by_inn("770123456789")
    assert result.status == CheckStatus.OK
    assert result.parsed["is_self_employed"] is True


async def test_fake_fns_npd_not_self_employed():
    result = await FakeFnsNpdSource(responses={"770123456789": False}).check_by_inn("770123456789")
    assert result.status == CheckStatus.NO_DATA


async def test_fake_fns_egrip_registered():
    result = await FakeFnsEgripSource(responses={"770123456789": True}).check_by_inn("770123456789")
    assert result.status == CheckStatus.OK
    assert result.parsed["is_individual_entrepreneur"] is True


# --- HTTP-источники (протокол с живой разведки T16, respx-моки) ---

_CONSENT_REDIRECT_HEADERS = {"Location": "/static/personal-data.html?svc=inn&from=%2Finn.do"}


def _inn_source() -> FnsInnHttpSource:
    return FnsInnHttpSource(
        redis=get_redis(), request_timeout_seconds=5, max_attempts=2,
        retry_backoff_seconds=0.01, throttle_interval_seconds=0.01,
    )


@respx.mock
async def test_http_fns_inn_full_flow_found():
    respx.get("https://service.nalog.ru/inn.do").mock(
        return_value=Response(302, headers=_CONSENT_REDIRECT_HEADERS)
    )
    respx.get("https://service.nalog.ru/static/personal-data.html").mock(return_value=Response(200, text="ok"))
    respx.post("https://service.nalog.ru/static/personal-data-proc.json").mock(return_value=Response(200))
    respx.post("https://service.nalog.ru/inn-new-proc.json").mock(
        side_effect=[
            Response(200, json={"requestId": "req-1", "token1": "t1", "captchaRequired": False}),
            Response(200, json={"state": 1, "inn": "770123456789"}),
        ]
    )

    result = await _inn_source().check(_SUBJECT)

    assert result.status == CheckStatus.OK
    assert result.parsed["inn"] == "770123456789"


@respx.mock
async def test_http_fns_inn_not_found_is_no_data():
    respx.get("https://service.nalog.ru/inn.do").mock(
        return_value=Response(302, headers=_CONSENT_REDIRECT_HEADERS)
    )
    respx.get("https://service.nalog.ru/static/personal-data.html").mock(return_value=Response(200, text="ok"))
    respx.post("https://service.nalog.ru/static/personal-data-proc.json").mock(return_value=Response(200))
    respx.post("https://service.nalog.ru/inn-new-proc.json").mock(
        side_effect=[
            Response(200, json={"requestId": "req-1", "token1": "t1", "captchaRequired": False}),
            Response(200, json={"state": 0}),
        ]
    )

    result = await _inn_source().check(_SUBJECT)

    assert result.status == CheckStatus.NO_DATA


@respx.mock
async def test_http_fns_inn_skips_consent_if_no_redirect():
    """Форма уже открылась без редиректа (согласие уже было в этой httpx-сессии) —
    ensure_consent не должен постить лишний раз."""
    respx.get("https://service.nalog.ru/inn.do").mock(return_value=Response(200, text="form"))
    consent_route = respx.post("https://service.nalog.ru/static/personal-data-proc.json").mock(
        return_value=Response(200)
    )
    respx.post("https://service.nalog.ru/inn-new-proc.json").mock(
        side_effect=[
            Response(200, json={"requestId": "req-1", "token1": "t1", "captchaRequired": False}),
            Response(200, json={"state": 0}),
        ]
    )

    result = await _inn_source().check(_SUBJECT)

    assert result.status == CheckStatus.NO_DATA
    assert consent_route.call_count == 0


@respx.mock
async def test_http_fns_inn_captcha_required_retries_then_source_unavailable():
    """Капча — рейт-лимит-триггерная (разведка T16): не решаем, повторяем
    через паузу, после исчерпания попыток — SOURCE_UNAVAILABLE, не автоотказ."""
    respx.get("https://service.nalog.ru/inn.do").mock(
        return_value=Response(302, headers=_CONSENT_REDIRECT_HEADERS)
    )
    respx.get("https://service.nalog.ru/static/personal-data.html").mock(return_value=Response(200, text="ok"))
    respx.post("https://service.nalog.ru/static/personal-data-proc.json").mock(return_value=Response(200))
    route = respx.post("https://service.nalog.ru/inn-new-proc.json").mock(
        return_value=Response(200, json={"captchaRequired": True})
    )

    result = await _inn_source().check(_SUBJECT)

    assert result.status == CheckStatus.SOURCE_UNAVAILABLE
    assert route.call_count == 2  # max_attempts=2, капча решена нигде не была


async def test_http_fns_inn_missing_required_fields_skips_network_call():
    """T16, живой прогон (ваше явное указание, п.2/3 правок): ERROR с
    конкретной причиной, не NO_DATA — NO_DATA означает «искали и не нашли»,
    а тут сайт вообще не запрашивался (отсутствует @respx.mock — любой
    реальный HTTP-вызов здесь обвалил бы тест без зарегистрированного мока)."""
    result = await _inn_source().check(Subject(last_name="Иванов"))
    assert result.status == CheckStatus.ERROR
    assert "имя" in result.error_text


@respx.mock
async def test_http_fns_npd_self_employed():
    respx.post("https://statusnpd.nalog.ru/api/v1/tracker/taxpayer_status").mock(
        return_value=Response(200, json={"status": True, "message": "является плательщиком НПД"})
    )
    source = FnsNpdHttpSource(redis=get_redis(), request_timeout_seconds=5, throttle_interval_seconds=0.01)

    result = await source.check_by_inn("770123456789", as_of=date(2026, 1, 1))

    assert result.status == CheckStatus.OK
    assert result.parsed["is_self_employed"] is True


@respx.mock
async def test_http_fns_npd_invalid_inn_is_source_unavailable():
    respx.post("https://statusnpd.nalog.ru/api/v1/tracker/taxpayer_status").mock(
        return_value=Response(422, json={"code": "validation.failed", "message": "Указан некорректный ИНН"})
    )
    source = FnsNpdHttpSource(redis=get_redis(), request_timeout_seconds=5, throttle_interval_seconds=0.01)

    result = await source.check_by_inn("0000000000", as_of=date(2026, 1, 1))

    assert result.status == CheckStatus.SOURCE_UNAVAILABLE


@respx.mock
async def test_http_fns_egrip_found():
    respx.post("https://egrul.nalog.ru/").mock(
        return_value=Response(200, json={"t": "token-1", "captchaRequired": False})
    )
    respx.get(url__regex=r"https://egrul\.nalog\.ru/search-result/token-1.*").mock(
        return_value=Response(200, json={"rows": [{"cnt": "1", "tot": "1", "k": "sprav-fl", "t": "token-1"}]})
    )
    source = FnsEgripHttpSource(
        redis=get_redis(), request_timeout_seconds=5, max_attempts=2,
        retry_backoff_seconds=0.01, throttle_interval_seconds=0.01,
    )

    result = await source.check_by_inn("770123456789")

    assert result.status == CheckStatus.OK
    assert result.parsed["is_individual_entrepreneur"] is True


@respx.mock
async def test_http_fns_egrip_not_registered_is_no_data():
    respx.post("https://egrul.nalog.ru/").mock(
        return_value=Response(200, json={"t": "token-1", "captchaRequired": False})
    )
    respx.get(url__regex=r"https://egrul\.nalog\.ru/search-result/token-1.*").mock(
        return_value=Response(200, json={"rows": [{"cnt": "0", "tot": "0", "k": "sprav-fl", "t": "token-1"}]})
    )
    source = FnsEgripHttpSource(
        redis=get_redis(), request_timeout_seconds=5, max_attempts=2,
        retry_backoff_seconds=0.01, throttle_interval_seconds=0.01,
    )

    result = await source.check_by_inn("770123456789")

    assert result.status == CheckStatus.NO_DATA


@respx.mock
async def test_http_fns_egrip_network_error_is_source_unavailable():
    respx.post("https://egrul.nalog.ru/").mock(side_effect=httpx.TimeoutException("timeout"))
    source = FnsEgripHttpSource(
        redis=get_redis(), request_timeout_seconds=5, max_attempts=1,
        retry_backoff_seconds=0.01, throttle_interval_seconds=0.01,
    )

    result = await source.check_by_inn("770123456789")

    assert result.status == CheckStatus.SOURCE_UNAVAILABLE
