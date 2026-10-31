"""БКИ «Спектрум Бюро» — сборка запроса, упаковка и подпись (app/sources/cbch.py).
Данные — синтетические."""

import io
import zipfile
from datetime import date
from xml.etree import ElementTree as ET

import pytest

from app.sources.base import Subject
from app.sources.cbch import (
    CbchRequestError,
    CbchRequestParams,
    CbchSignError,
    CryptoProSigner,
    FakeSigner,
    build_package,
    build_request_xml,
    package_base_name,
)

SUBJECT = Subject(
    last_name="Тестов", first_name="Пётр", middle_name="Иванович", birth_date=date(1985, 3, 5),
    passport_series="8201", passport_number="123456", passport_issue_date=date(2005, 4, 1),
)
PARAMS = CbchRequestParams(
    report_code="1", purpose_code="9", external_number="DIALOG-42", consent_date=date(2026, 10, 30),
    consent_term_code="1",
)


def test_request_xml_matches_bureau_example_structure():
    raw = build_request_xml(SUBJECT, PARAMS)
    assert raw.startswith(b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
    root = ET.fromstring(raw)
    assert root.tag == "Request"
    assert [child.tag for child in root] == ["requestInfo", "agreement", "subjectInfo"]
    assert root.findtext("requestInfo/reportCode") == "1"
    assert root.findtext("requestInfo/purposeCode") == "9"
    assert root.findtext("requestInfo/externalNumber") == "DIALOG-42"
    assert root.findtext("agreement/code") == "GIVEN"
    assert root.findtext("agreement/startDate") == "2026-10-30"
    assert root.findtext("agreement/termCode") == "1"
    assert root.findtext("agreement/responsibilityAware") == "AWARE"
    assert root.findtext("subjectInfo/code") == "PERSON"
    person = root.find("subjectInfo/person")
    assert person.findtext("lastName") == "Тестов"
    assert person.findtext("middleName") == "Иванович"
    assert person.findtext("birthDate") == "1985-03-05"
    assert person.findtext("doc/code") == "21"
    assert person.findtext("doc/series") == "8201"
    assert person.findtext("doc/number") == "123456"
    assert person.findtext("doc/issueDate") == "2005-04-01"
    assert person.find("inn") is None  # ИНН — только если известен


def test_request_xml_refuses_incomplete_subject():
    with pytest.raises(CbchRequestError, match="дата рождения"):
        build_request_xml(Subject(last_name="Тестов", first_name="Пётр", passport_series="8201",
                                  passport_number="123456"), PARAMS)
    bad = Subject(last_name="Тестов", first_name="Пётр", birth_date=date(1985, 3, 5),
                  passport_series="82", passport_number="123456")
    with pytest.raises(CbchRequestError, match="4 цифры"):
        build_request_xml(bad, PARAMS)


def test_package_names_and_contents():
    assert package_base_name(date(2026, 10, 31), 7) == "20261031_00007"
    with pytest.raises(CbchRequestError):
        package_base_name(date(2026, 10, 31), 100000)
    xml = build_request_xml(SUBJECT, PARAMS)
    name, data = build_package([xml], day=date(2026, 10, 31), number=7)
    assert name == "20261031_00007.zip"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.namelist() == ["20261031_00007_1.xml"]
        assert archive.read("20261031_00007_1.xml") == xml


async def test_fake_signer_and_cryptopro_command():
    assert (await FakeSigner().sign(b"zip")).endswith(b"zip")
    signer = CryptoProSigner(thumbprint="AB12", pin="0000", cryptcp_path="/opt/cprocsp/bin/amd64/cryptcp")
    command = signer.command("in.zip", "in.zip.sig")
    assert command[:6] == ["/opt/cprocsp/bin/amd64/cryptcp", "-sign", "-thumbprint", "AB12", "-der", "-nochain"]
    assert command[-2:] == ["in.zip", "in.zip.sig"]
    assert "-detached" not in command
    assert "-detached" in CryptoProSigner(thumbprint="AB12", detached=True).command("a", "b")


async def test_cryptopro_signer_reports_missing_tool_and_thumbprint():
    with pytest.raises(CbchSignError, match="отпечаток"):
        CryptoProSigner(thumbprint="")
    signer = CryptoProSigner(thumbprint="AB12", cryptcp_path="/nonexistent/cryptcp")
    with pytest.raises(CbchSignError, match="не найдена утилита"):
        await signer.sign(b"zip")
