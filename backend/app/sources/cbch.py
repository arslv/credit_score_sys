"""БКИ «Спектрум Бюро» (cbch.ru, «Credit History Data») — запрос кредитного
отчёта: сборка XML, упаковка в ZIP и подпись КЭП через КриптоПро.

У бюро нет HTTP-API: запрос — файл-пакет, который кладётся в каталог `inbox`
на SFTP бюро (через защищённый канал КриптоПро NGate), квитанция и отчёт
появляются позже в `outbox` (docs/bki_cbch.md). Здесь — только то, что
описано в публичной документации бюро «Порядок направления запросов на
получение КО» (https://chd.cbch.ru/docs/credit-report/procedure-of-sending):

- один запрос — один XML-файл в UTF-8, корень <Request>;
- файл в пакете: ГГГГММДД_NNNNN_R.xml (R — номер файла в пакете);
- пакет: ГГГГММДД_NNNNN.zip, после подписи — ГГГГММДД_NNNNN.zip.sig;
  исходящий номер NNNNN уникален, повторное имя бюро отклоняет;
- подпись: CMS/PKCS#7, DER, ГОСТ Р 34.10-2012 / 34.11-2012.

Транспорт (SFTP через NGate), опрос `outbox`, разбор квитанций и самого
отчёта — следующим шагом: формат отчёта (раздел 4 документации) в открытом
доступе не опубликован, его нужно получить у бюро.

Подпись делается на сервере: ключ организации стоит в КриптоПро CSP на
сервере, сотрудникам на своих компьютерах ничего ставить не нужно — они
только нажимают «Запросить КИ» в админке."""

import asyncio
import io
import os
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from xml.etree import ElementTree as ET

from app.sources.base import Subject

# Документ, удостоверяющий личность, — паспорт гражданина РФ (справочник бюро).
_DOC_CODE_RU_PASSPORT = "21"


class CbchRequestError(Exception):
    """Запрос нельзя собрать: не хватает обязательных данных субъекта или
    параметров. Текст — для сотрудника, без персданных."""


@dataclass(frozen=True)
class CbchRequestParams:
    report_code: str  # 1 — кредитный отчёт, 2 — информационная часть КИ (bki.report_types.*.provider_code)
    purpose_code: str  # цель запроса по справочнику бюро (9 — иной потребительский договор)
    external_number: str  # наш номер запроса — вернётся в квитанции и отчёте
    consent_date: date  # дата согласия клиента на запрос КИ
    consent_term_code: str  # 1 — 6 месяцев, 2 — 1 год, 3 — на срок договора
    inn: str | None = None


def _text(parent: ET.Element, tag: str, value: str | None) -> None:
    child = ET.SubElement(parent, tag)
    child.text = value or ""


def build_request_xml(subject: Subject, params: CbchRequestParams) -> bytes:
    """XML одного запроса по физлицу — порядок и вложенность как в примере
    бюро. Без ФИО, даты рождения или паспорта запрос не собираем: бюро
    ищет субъекта по ним, неполный запрос — платный пустой ответ."""
    missing = [
        label
        for label, value in (
            ("фамилия", subject.last_name),
            ("имя", subject.first_name),
            ("дата рождения", subject.birth_date),
            ("серия паспорта", subject.passport_series),
            ("номер паспорта", subject.passport_number),
        )
        if not value
    ]
    if missing:
        raise CbchRequestError(f"не хватает данных для запроса в бюро: {', '.join(missing)}")
    series, number = subject.passport_series or "", subject.passport_number or ""
    if not (len(series) == 4 and series.isdigit() and len(number) == 6 and number.isdigit()):
        raise CbchRequestError("серия паспорта — 4 цифры, номер — 6 цифр")

    root = ET.Element("Request")
    info = ET.SubElement(root, "requestInfo")
    _text(info, "reportCode", params.report_code)
    _text(info, "purposeCode", params.purpose_code)
    _text(info, "purposeComment", None)
    _text(info, "externalNumber", params.external_number)

    agreement = ET.SubElement(root, "agreement")
    _text(agreement, "code", "GIVEN")
    _text(agreement, "startDate", params.consent_date.isoformat())
    _text(agreement, "termCode", params.consent_term_code)
    _text(agreement, "responsibilityAware", "AWARE")

    subject_info = ET.SubElement(root, "subjectInfo")
    _text(subject_info, "code", "PERSON")
    person = ET.SubElement(subject_info, "person")
    _text(person, "lastName", subject.last_name)
    _text(person, "firstName", subject.first_name)
    if subject.middle_name:
        _text(person, "middleName", subject.middle_name)
    _text(person, "birthDate", subject.birth_date.isoformat() if subject.birth_date else None)
    doc = ET.SubElement(person, "doc")
    _text(doc, "code", _DOC_CODE_RU_PASSPORT)
    _text(doc, "series", series)
    _text(doc, "number", number)
    if subject.passport_issue_date:
        _text(doc, "issueDate", subject.passport_issue_date.isoformat())
    if params.inn:
        _text(person, "inn", params.inn)

    body = ET.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + body).encode("utf-8")


def package_base_name(day: date, number: int) -> str:
    """ГГГГММДД_NNNNN — дата отправки и уникальный исходящий номер."""
    if not 0 < number <= 99999:
        raise CbchRequestError("исходящий номер пакета — от 1 до 99999")
    return f"{day:%Y%m%d}_{number:05d}"


def build_package(xml_files: list[bytes], *, day: date, number: int) -> tuple[str, bytes]:
    """ZIP с запросами: (имя архива ГГГГММДД_NNNNN.zip, байты). Файлы внутри —
    ГГГГММДД_NNNNN_R.xml, R с единицы."""
    if not xml_files:
        raise CbchRequestError("пакет без запросов")
    base = package_base_name(day, number)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, content in enumerate(xml_files, start=1):
            archive.writestr(f"{base}_{index}.xml", content)
    return f"{base}.zip", buffer.getvalue()


# --- подпись -------------------------------------------------------------------


class CbchSignError(Exception):
    """Подпись не получилась (КриптоПро не установлен, ключ не найден, PIN
    неверный). Текст — техническая причина, без персданных."""


class Signer(Protocol):
    async def sign(self, data: bytes) -> bytes:
        """Подписанный файл (.sig) для данных пакета."""
        ...


class CryptoProSigner:
    """Подпись утилитой КриптоПро cryptcp на сервере: сертификат организации
    выбирается по отпечатку (thumbprint), ключ — в контейнере КриптоПро CSP
    на сервере. Формат — CMS, DER; detached=True — открепленная подпись.

    В документации бюро противоречие: «присоединённая» подпись, но файл
    .zip.sig — уточнить у бюро; по умолчанию присоединённая. Ключи cryptcp
    сверить на установленной версии КриптоПро при первом подключении."""

    def __init__(
        self, *, thumbprint: str, pin: str | None = None, detached: bool = False,
        cryptcp_path: str = "/opt/cprocsp/bin/amd64/cryptcp", timeout_seconds: float = 60,
    ) -> None:
        if not thumbprint:
            raise CbchSignError("не задан отпечаток сертификата для подписи (CBCH_SIGN_THUMBPRINT)")
        self._thumbprint = thumbprint
        self._pin = pin
        self._detached = detached
        self._cryptcp = cryptcp_path
        self._timeout = timeout_seconds

    def command(self, source: str, target: str) -> list[str]:
        args = [self._cryptcp, "-sign", "-thumbprint", self._thumbprint, "-der", "-nochain"]
        if self._detached:
            args.append("-detached")
        if self._pin:
            args += ["-pin", self._pin]
        return args + [source, target]

    async def sign(self, data: bytes) -> bytes:
        with tempfile.TemporaryDirectory() as workdir:
            source = os.path.join(workdir, "package.zip")
            target = source + ".sig"
            with open(source, "wb") as handle:
                handle.write(data)
            try:
                process = await asyncio.create_subprocess_exec(
                    *self.command(source, target),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise CbchSignError(f"не найдена утилита КриптоПро: {self._cryptcp}") from exc
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=self._timeout)
            except asyncio.TimeoutError as exc:
                process.kill()
                raise CbchSignError("КриптоПро не ответил вовремя (ключ на токене ждёт PIN?)") from exc
            if process.returncode != 0 or not os.path.exists(target):
                # В stderr cryptcp — коды ошибок КриптоПро, без содержимого пакета.
                detail = stderr.decode("utf-8", "replace").strip()[-300:]
                raise CbchSignError(f"cryptcp завершился с кодом {process.returncode}: {detail}")
            with open(target, "rb") as handle:
                return handle.read()


class FakeSigner:
    """Для тестов и дев-режима: «подпись» — маркер плюс данные, без КриптоПро."""

    MARKER = b"FAKE-CMS-SIGNATURE\n"

    async def sign(self, data: bytes) -> bytes:
        return self.MARKER + data
