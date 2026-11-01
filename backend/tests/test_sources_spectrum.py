"""БКИ через Спектрум Дату: разбор отчёта «Кредитный рейтинг с детализацией»
(P.0191.CRR.HIS — примеры ответов из описания блока в кабинете), признаки
по сводке-диапазонам, провайдер на подменном транспорте и проверки из
подробного отчёта рядом с нашими."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.engine.bki_features import compute_bki_features
from app.engine.stage_b_pipeline import run_stage_b
from app.models import BkiBureauCheck, BkiReport, Check, Decision
from app.sources import spectrum
from app.sources.base import CheckStatus, Subject
from app.sources.bki import BkiProviderResult, deserialize_report, serialize_report
from app.sources.spectrum import BureauCheck, SpectrumDataBkiProvider, parse_credit_rating, request_data
from tests.conftest import unique_valid_passport_series
from tests.test_engine_pipeline import active_ruleset  # noqa: F401
from tests.test_engine_stage_b import (
    _link_applicant_to_client,
    _mark_fssp_checked,
    _prepare_application_at_stage_b,
)

# Пример из описания блока (кабинет Спектрум Даты), без изменений по сути.
REPORT = {
    "data": [{
        "report_type_uid": "P.0191.CRR.HIS_test_report@test_domain",
        "progress_ok": 1, "progress_wait": 0, "progress_error": 0,
        "content": {"check_person": {"credit_history": {
            "summary": {"activeAccounts": {"borrowerCount": "3-4", "guarantorCount": "0-2"},
                        "activeDelay": "0.0-999.99", "treatmentsInBanks": 100},
            "bills": {"activeAccounts": {"borrowerCount": "3-4", "guarantorCount": "0-2"},
                      "activeCreditsSumLimit": "1000000.0-1999999.99", "averagePayment": "100000.0-149999.99",
                      "lastRepaymentDate": "2069-12-01"},
            "credits": {"hasBadDebtOrPassedToCollection": False,
                        "creditTypeInfosOpen": [{"type": 2, "typeDescription": "Потребительский кредит"},
                                                {"type": 5, "typeDescription": "Кредитная карта"}],
                        "creditTypeInfosClosed": [{"type": 1, "typeDescription": "Ипотека"}]},
            "activeCreditsDelays": {"maxCurrentDelay": "Оплата без просрочек",
                                    "maxHistoryDelay": "Просрочка от 30 до 59 дней", "sumDelay": "0.0-999.99"},
            "treatmentsInBanks": {"forLast7Days": 0, "forLast14Days": 0, "forLastMonth": 1, "all": 100},
            "score": {"value": "736", "description": "балл средний, нормальная финансовая дисциплина"},
            "src_meta": {"state": "OK", "detailed": ""},
        }}},
    }],
}
NOT_FOUND = {"data": [{"content": {"check_person": {"credit_history": {"src_meta": {"state": "OK", "detailed": ""}}}}}]}


def test_parse_credit_rating_from_documentation_example():
    status, report = parse_credit_rating(REPORT)

    assert status is CheckStatus.OK
    assert report.score == 736
    s = report.summary
    assert s["active_accounts"] == ["3", "4"]
    assert s["average_payment"] == ["100000.0", "149999.99"]
    assert s["current_overdue_days"] == 0
    assert s["max_overdue_days"] == 30
    assert s["bad_debt"] is False
    assert s["open_credit_types"] == [2, 5]
    assert (s["inquiries_7d"], s["inquiries_1mo"], s["inquiries_all"]) == (0, 1, 100)
    # Переживает сохранение в bki_reports и чтение из кэша.
    assert deserialize_report(serialize_report(report)) == report


def test_no_credit_history_is_no_data_and_failed_source_is_unavailable():
    status, report = parse_credit_rating(NOT_FOUND)
    assert status is CheckStatus.NO_DATA and report.score is None and report.summary is None

    failed = {"data": [{"content": {"check_person": {"credit_history": {"src_meta": {"state": "ERROR"}}}}}]}
    assert parse_credit_rating(failed)[0] is CheckStatus.SOURCE_UNAVAILABLE
    assert parse_credit_rating({"data": []})[0] is CheckStatus.ERROR


@pytest.mark.parametrize(("bound", "payment"), [("upper", "149999.99"), ("lower", "100000.0")])
def test_features_from_ranges_use_configured_bound(bound, payment):
    _status, report = parse_credit_rating(REPORT)
    features = compute_bki_features(
        report, monthly_income=Decimal("300000"), declared_monthly_obligations=Decimal("50000"),
        installment_payment=Decimal("5000"), today=date(2026, 11, 1), range_bound=bound,
    )["features"]

    assert features["pti"] == float((Decimal(payment) + Decimal("5000")) / Decimal("300000"))
    assert features["bki_active_contracts_count"] == (4 if bound == "upper" else 3)
    assert features["bki_max_overdue_6mo"] == 0  # текущая просрочка — во все окна
    assert features["bki_max_overdue_ever_days"] == 30
    assert features["bki_overdue_90_plus_count"] is None  # в сводке нет — не «ноль»
    assert features["bki_inquiries_1mo"] == 1 and features["bki_inquiries_3mo"] is None
    assert features["bki_bad_debt"] is False


def _subject() -> Subject:
    return Subject(
        last_name="Иванов", first_name="Иван", middle_name="Иванович", birth_date=date(1991, 2, 11),
        passport_series="1234", passport_number="123456", passport_issue_date=date(2018, 5, 30),
    )


def test_request_data_matches_documented_format():
    assert request_data(_subject()) == {
        "last_name": "Иванов", "first_name": "Иван", "patronymic": "Иванович",
        "birth": "11.02.1991", "passport": "1234 123456", "passport_date": "30.05.2018",
    }


class _Transport:
    def __init__(self, response=None, error: Exception | None = None):
        self.calls: list[tuple[str, dict]] = []
        self._response = response
        self._error = error

    async def fetch_report(self, report_type_uid, data):
        self.calls.append((report_type_uid, data))
        if self._error:
            raise self._error
        return self._response


async def test_provider_requests_package_and_never_raises():
    transport = _Transport(REPORT)
    provider = SpectrumDataBkiProvider(transport)

    result = await provider.request_report(_subject(), report_code="CRR@domain")
    assert result.status is CheckStatus.OK and result.report.score == 736
    assert result.raw == REPORT  # правило 3 — сырой ответ как есть
    assert transport.calls[0][0] == "CRR@domain"

    assert (await provider.request_report(_subject(), report_code=None)).status is CheckStatus.ERROR
    down = await SpectrumDataBkiProvider(_Transport(error=TimeoutError("timeout"))).request_report(
        _subject(), report_code="CRR@domain",
    )
    assert down.status is CheckStatus.SOURCE_UNAVAILABLE

    # Подробный пакет: кредитный рейтинг + проверки (ФССП, банкротство).
    result = await SpectrumDataBkiProvider(_Transport(PREMIUM)).request_report(_subject(), report_code="PREM@domain")
    assert result.status is CheckStatus.OK and result.report.score == 736
    assert [(c.source, c.status) for c in result.extra_checks] == [("fssp", "hit"), ("fedresurs", "hit")]


# Примеры из описаний блоков P.0035.SSP.HIS и P.0124.BNK.HIS-SHT (кабинет),
# только нужные поля.
FSSP_BLOCK = {
    "size": 2,
    "items": [
        {"epNumber": "01010/11/10101-ИП", "weSubjTypeName": "Страховые взносы, включая пени",
         "debtBalanceTotal": 0, "unloadStatus": "C", "endDate": "2017-02-10", "isOpen": False,
         "weRisedate": "2016-11-08", "bailiffOfficeTerotdel": "Клинцовское РОСП"},
        {"epNumber": "01010/11/10102-ИП", "weSubjTypeName": "Страховые взносы, включая пени",
         "debtBalanceTotal": 500, "unloadStatus": "U", "endDate": "", "isOpen": True,
         "weRisedate": "2016-11-18", "bailiffOfficeTerotdel": "Клинцовское РОСП"},
    ],
    "openCount": 1, "closeCount": 1, "limitReached": False,
}
BANKRUPTCY_BLOCK = {
    "debtor": {"lastName": "Фамилия", "inn": "12345678"},
    "actualCaseStatus": "IN_PROGRESS", "sourceType": "FINAL_REPORT", "debtorStatus": "IS_LISTED",
    "legalCaseNumbers": ["123456"], "courtName": "Арбитражный суд Тамбовской области",
    "legalCaseClosed": False, "procedureType": "EXTERNAL_MANAGEMENT",
    "lastMessageName": "Сведения об отчете оценщика", "lastMessageDate": "2020-01-01T00:00:00.0Z",
}
PREMIUM = {"data": [{
    "state": {"sources": [
        {"_id": "check_person/executive_proceeding_base", "state": "OK"},
        {"_id": "check_person/fedresurs_short", "state": "OK"},
    ]},
    "content": {"check_person": {
        **REPORT["data"][0]["content"]["check_person"],
        "proceeding_v2": {"executive_v2": FSSP_BLOCK},
        "bankruptcy_short": BANKRUPTCY_BLOCK,
    }},
}]}


def _premium(content: dict, states: dict[str, str]) -> dict:
    return {"data": [{
        "state": {"sources": [{"_id": k, "state": v} for k, v in states.items()]},
        "content": {"check_person": content},
    }]}


def test_bureau_fssp_counts_only_open_proceedings():
    fssp, bankruptcy = spectrum.extract_bureau_checks(PREMIUM)

    assert fssp.source == "fssp" and fssp.status == "hit"
    assert fssp.block == "check_person.proceeding_v2.executive_v2"
    assert fssp.parsed["open_count"] == 1 and fssp.parsed["open_debt_total"] == "500"
    assert [i["ep_number"] for i in fssp.parsed["open_items"]] == ["01010/11/10102-ИП"]  # закрытое — не в счёт
    assert "1" in fssp.parsed["summary"] and "500" in fssp.parsed["summary"]

    assert bankruptcy.source == "fedresurs" and bankruptcy.status == "hit"
    assert bankruptcy.parsed["debtor_status"] == "IS_LISTED"
    assert bankruptcy.parsed["case_numbers"] == ["123456"]
    assert "дело идёт" in bankruptcy.parsed["summary"]


def test_bureau_nothing_found_unavailable_and_not_in_package():
    ok = {"check_person/executive_proceeding_base": "OK", "check_person/fedresurs_short": "OK"}

    # Только закрытые ИП, блока банкротства нет при ответившем источнике — чисто.
    closed_only = {**FSSP_BLOCK, "items": FSSP_BLOCK["items"][:1], "openCount": 0}
    checks = spectrum.extract_bureau_checks(_premium({"proceeding_v2": {"executive_v2": closed_only}}, ok))
    assert [(c.source, c.status) for c in checks] == [("fssp", "ok"), ("fedresurs", "ok")]
    assert checks[1].parsed["summary"] == "в реестре банкротств нет"

    # Источник не ответил — «не удалось», не «чисто».
    down = spectrum.extract_bureau_checks(_premium({}, {**ok, "check_person/fedresurs_short": "ERROR"}))
    assert [(c.source, c.status) for c in down] == [("fssp", "ok"), ("fedresurs", "source_unavailable")]

    # «Обычный» пакет — этих источников в отчёте нет, проверок нет.
    assert spectrum.extract_bureau_checks(REPORT) == []


class _ProviderWithBureauChecks:
    is_configured = True

    async def request_report(self, subject, *, report_code=None):
        _status, report = parse_credit_rating(REPORT)
        return BkiProviderResult(
            status=CheckStatus.OK, report=report, raw=REPORT,
            extra_checks=[BureauCheck(source="fssp", block="fssp_block", status="ok", parsed={"items": []})],
        )


async def test_bureau_checks_stored_next_to_ours_and_shown_in_card(
    admin_client, partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    from tests._admin_helpers import create_employee, login

    _external_id, application_id = await _prepare_application_at_stage_b(
        partner_admin, partner_client, storage, session_factory,
        stage_b_payload={
            "deal": {"goods_category_code": "household_appliances", "goods_price": 60000.0, "down_payment": 6000.0,
                     "term_months": 12, "tariff": "standard"},
            "employment": {"type": "employed"}, "income": {"declared_monthly": "1000000"},
        },
    )
    await _link_applicant_to_client(
        session_factory, application_id, series=unique_valid_passport_series(),
        number=f"{uuid.uuid4().int % 1000000:06d}",
    )
    await _mark_fssp_checked(session_factory, application_id)

    await run_stage_b(
        session_factory, application_id, partner_client=partner_client, bki_provider=_ProviderWithBureauChecks(),
    )

    async with session_factory() as session:
        bureau = (await session.execute(
            select(BkiBureauCheck).where(BkiBureauCheck.application_id == application_id)
        )).scalars().all()
        ours = (await session.execute(
            select(Check).where(Check.application_id == application_id, Check.source == "fssp")
        )).scalars().all()
        report_row = (await session.execute(
            select(BkiReport).where(BkiReport.application_id == application_id)
        )).scalar_one()
        decision = (await session.execute(
            select(Decision).where(Decision.application_id == application_id)
        )).scalar_one()
    assert [(b.source, b.status) for b in bureau] == [("fssp", "ok")]
    assert len(ours) == 1  # наша проверка ФССП — как была, бюро её не подменяет
    assert report_row.report_raw["raw"] == REPORT
    assert decision.score == Decimal("736.00")

    await create_employee(session_factory, email="spectrum-viewer@example.com", role="viewer")
    await login(admin_client, "spectrum-viewer@example.com")
    detail = await admin_client.get(f"/api/applications/{application_id}")
    assert detail.status_code == 200, detail.text
    assert [(c["source"], c["status"], c["block"]) for c in detail.json()["bureau_checks"]] == [
        ("fssp", "ok", "fssp_block"),
    ]
