import base64
from datetime import date

import httpx
import respx
from httpx import Response

from app.engine.fssp import summarize_productions
from app.engine.schema import FsspCategory, FsspClassificationRule
from app.redis import get_redis
from app.sources.base import CheckStatus, Subject
from app.sources.fssp import (
    FakeFsspCaptchaSource,
    FsspCaptchaHttpSource,
    FsspSearchError,
    FsspValidationError,
    parse_fssp_result_html,
)

_SUBJECT = Subject(last_name="Иванов", first_name="Иван", middle_name="Иванович", birth_date=date(1990, 1, 15))
_CLASSIFICATION = [
    FsspClassificationRule(category=FsspCategory.NOISE, match=["гибдд"]),
    FsspClassificationRule(category=FsspCategory.CREDIT_DEBT, match=["кредит", "займ"]),
]

_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _jsonp(data_html: str) -> str:
    import json

    return "(" + json.dumps({"data": data_html, "err": "", "e": ""}) + ");"


def _captcha_html(code_id: str) -> str:
    return (
        f'<div id="captcha-popup"><form action="https://is-go.fssp.gov.ru" '
        f'url="/ajax_search?code_id={code_id}">'
        f'<img src="data:image/png;base64,{_TINY_PNG_B64}"/></form></div>'
    )


def _not_found_html() -> str:
    return '<div class="iss"><div class="results"><div class="empty">По вашему запросу ничего не найдено</div></div></div>'


def _wrong_code_html() -> str:
    return '<div class="iss"><div class="results"><div class="empty">Количество неверных попыток ввода кода превышено. Попробуйте позже</div></div></div>'


def _found_html(productions: list[tuple[str, str]]) -> str:
    blocks = "".join(
        f'<div class="production"><div class="field" data-name="predmet">{subj}</div>'
        f'<div class="field" data-name="summa">{amount}</div></div>'
        for subj, amount in productions
    )
    return f'<div class="iss"><div class="results">{blocks}</div></div>'


# --- парсер HTML (синтетика — см. оговорку в app/sources/fssp.py) -------------


def test_parse_fssp_result_html_extracts_productions():
    html = _found_html([("Взыскание по кредитному договору", "50000"), ("Штраф ГИБДД", "3000")])
    productions = parse_fssp_result_html(html)
    assert len(productions) == 2
    assert productions[0]["subject"] == "Взыскание по кредитному договору"
    assert productions[0]["amount"] == "50000"


# --- FakeFsspCaptchaSource ------------------------------------------------------


async def test_fake_search_returns_captcha_and_code_id():
    source = FakeFsspCaptchaSource()
    challenge = await source.search(_SUBJECT, region_code="05")
    assert challenge.code_id
    assert challenge.captcha_png


async def test_fake_search_missing_required_field_raises_validation_error():
    """T16, живой прогон (ваше явное указание, п.2/3 правок): пустой
    обязательный Subject — своя ошибка, не «сайт недоступен», и фейк должен
    падать так же, как настоящий источник (иначе баг не поймать тестом)."""
    source = FakeFsspCaptchaSource()
    try:
        await source.search(Subject(last_name="Иванов"), region_code="05")
        assert False, "должно было бросить FsspValidationError"
    except FsspValidationError as exc:
        assert "имя" in str(exc) or "дата рождения" in str(exc)


async def test_fake_submit_correct_code_not_found():
    source = FakeFsspCaptchaSource(valid_code="12345")
    challenge = await source.search(_SUBJECT, region_code="05")
    outcome = await source.submit_code(
        code_id=challenge.code_id, code="12345", subject=_SUBJECT, region_code="05",
        
    )
    assert outcome.kind == "ok"
    assert outcome.check_result.status == CheckStatus.OK
    assert outcome.check_result.parsed["productions"] == []


async def test_fake_submit_correct_code_with_hit():
    productions = [{"subject": "Взыскание по кредитному договору", "amount": "50000"}]
    source = FakeFsspCaptchaSource(valid_code="12345", productions=productions)
    challenge = await source.search(_SUBJECT, region_code="05")
    outcome = await source.submit_code(
        code_id=challenge.code_id, code="12345", subject=_SUBJECT, region_code="05",
    )
    assert outcome.check_result.status == CheckStatus.HIT
    # Классификация — не в source (убрала бы циклический импорт sources<->engine),
    # её считает вызывающий код (app/api/fssp.py) поверх сырых productions.
    features = summarize_productions(outcome.check_result.parsed["productions"], _CLASSIFICATION)
    assert features["fssp_credit_debt_amount"] == 50000.0


async def test_fake_submit_wrong_code():
    source = FakeFsspCaptchaSource(valid_code="12345")
    challenge = await source.search(_SUBJECT, region_code="05")
    outcome = await source.submit_code(
        code_id=challenge.code_id, code="00000", subject=_SUBJECT, region_code="05",
        
    )
    assert outcome.kind == "wrong_code"


# --- FsspCaptchaHttpSource (протокол с живой разведки T16) ---------------------


def _http_source() -> FsspCaptchaHttpSource:
    return FsspCaptchaHttpSource(redis=get_redis(), request_timeout_seconds=5, throttle_interval_seconds=0.01)


@respx.mock
async def test_http_search_returns_captcha():
    respx.get("https://is-go.fssp.gov.ru/ajax_search").mock(
        return_value=Response(200, text=_jsonp(_captcha_html("code-id-1")))
    )
    challenge = await _http_source().search(_SUBJECT, region_code="05")
    assert challenge.code_id == "code-id-1"
    assert base64.b64decode(_TINY_PNG_B64) == challenge.captcha_png


@respx.mock
async def test_http_search_missing_required_field_raises_without_network_call():
    """T16, живой прогон: пустой обязательный Subject -> FsspValidationError,
    сайт вообще не запрашивается (respx без зарегистрированного мока — любой
    реальный вызов здесь обвалил бы тест)."""
    incomplete = Subject(last_name="Иванов", first_name="Иван")  # нет даты рождения
    try:
        await _http_source().search(incomplete, region_code="05")
        assert False, "должно было бросить FsspValidationError"
    except FsspValidationError as exc:
        assert "дата рождения" in str(exc)


@respx.mock
async def test_http_search_network_error_raises():
    respx.get("https://is-go.fssp.gov.ru/ajax_search").mock(side_effect=httpx.TimeoutException("timeout"))
    try:
        await _http_source().search(_SUBJECT, region_code="05")
        assert False, "должно было бросить FsspSearchError"
    except FsspSearchError as exc:
        assert exc.raw_response is None  # сети не было вовсе — нет и ответа (п.5 ваших правок)


@respx.mock
async def test_http_search_no_captcha_in_response_raises_with_raw_response():
    """T16, живой прогон — ровно то, что вы увидели: сайт реально ответил
    (200, валидный JSONP), просто без капчи. Сообщение должно отличаться от
    сетевой ошибки (п.5 ваших правок), и сырой ответ должен сохраниться
    (правило 3 CLAUDE.md), чтобы можно было разобраться, что сайт вернул."""
    respx.get("https://is-go.fssp.gov.ru/ajax_search").mock(
        return_value=Response(200, text=_jsonp("<html>неожиданная страница</html>"))
    )
    try:
        await _http_source().search(_SUBJECT, region_code="05")
        assert False, "должно было бросить FsspSearchError"
    except FsspSearchError as exc:
        assert "без капчи" in str(exc)
        assert "недоступен" not in str(exc)  # не путаем с сетевой ошибкой
        assert exc.raw_response is not None
        assert exc.raw_response["data"] == "<html>неожиданная страница</html>"


@respx.mock
async def test_http_submit_code_not_found():
    respx.get("https://is-go.fssp.gov.ru/ajax_search").mock(
        return_value=Response(200, text=_jsonp(_not_found_html()))
    )
    outcome = await _http_source().submit_code(
        code_id="code-id-1", code="12345", subject=_SUBJECT, region_code="05",
    )
    assert outcome.kind == "ok"
    assert outcome.check_result.status == CheckStatus.OK


@respx.mock
async def test_http_submit_code_wrong_code():
    respx.get("https://is-go.fssp.gov.ru/ajax_search").mock(
        return_value=Response(200, text=_jsonp(_wrong_code_html()))
    )
    outcome = await _http_source().submit_code(
        code_id="code-id-1", code="00000", subject=_SUBJECT, region_code="05",
    )
    assert outcome.kind == "wrong_code"


@respx.mock
async def test_http_submit_code_hit():
    respx.get("https://is-go.fssp.gov.ru/ajax_search").mock(
        return_value=Response(
            200, text=_jsonp(_found_html([("Взыскание по кредитному договору", "50000")]))
        )
    )
    outcome = await _http_source().submit_code(
        code_id="code-id-1", code="12345", subject=_SUBJECT, region_code="05",
    )
    assert outcome.kind == "ok"
    assert outcome.check_result.status == CheckStatus.HIT
    features = summarize_productions(outcome.check_result.parsed["productions"], _CLASSIFICATION)
    assert features["fssp_credit_debt_amount"] == 50000.0


@respx.mock
async def test_http_submit_code_unparseable_is_error_not_autoreject():
    respx.get("https://is-go.fssp.gov.ru/ajax_search").mock(
        return_value=Response(200, text=_jsonp('<div class="iss">что-то неожиданное, не похоже ни на что</div>'))
    )
    outcome = await _http_source().submit_code(
        code_id="code-id-1", code="12345", subject=_SUBJECT, region_code="05",
    )
    assert outcome.kind == "ok"
    assert outcome.check_result.status == CheckStatus.ERROR
    assert outcome.check_result.response_raw is not None  # сырой ответ сохранён — оператор видит его


@respx.mock
async def test_http_submit_code_network_error_is_source_unavailable():
    respx.get("https://is-go.fssp.gov.ru/ajax_search").mock(side_effect=httpx.TimeoutException("timeout"))
    outcome = await _http_source().submit_code(
        code_id="code-id-1", code="12345", subject=_SUBJECT, region_code="05",
    )
    assert outcome.kind == "source_unavailable"
    assert outcome.check_result.status == CheckStatus.SOURCE_UNAVAILABLE
