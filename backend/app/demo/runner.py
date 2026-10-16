"""Прогон плана демо-сида (T23) по хронологии событий.

Каждое событие выполняется под виртуальными часами (clock.virtual_time) в
его момент — заявка 45-дневной давности получает created_at/решение/историю
статусов 45-дневной давности без единого UPDATE по append-only таблицам.

Всё — штатными путями, как в бою:
- тела вебхуков собирает fake_partner (/_test/compose-*: синтетические фото
  паспорта, «Принять на рассмотрение»), доставка — в наш FastAPI-app в этом
  же процессе (httpx.ASGITransport), под виртуальным временем;
- стадии А и Б, проверки ФНС — те же функции, что зовут Celery-задачи
  (app.tasks.*), тоже в этом процессе: .delay() перехвачен и складывает
  задачу в очередь сида. Поэтому worker и beat на время сида должны быть
  остановлены (иначе подхватят заявки по расписанию с реальным временем —
  cli/seed_demo.py это проверяет);
- OCR — FakeOcrProvider с полями, нарисованными на фото; БКИ — FakeBkiProvider
  с отчётом из плана (или «не подключено»);
- ФССП, комплаенс, версии правил, справочники, сотрудники, импорт — через
  API админки под демо-сотрудниками нужных ролей (аудит пишется как обычно);
- решения уходят на настоящий /partner/webhook fake_partner; «оператор
  изменил статус сам» — режим conflict fake_partner (409), как в тестах T18.
"""

import heapq
import itertools
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock
from uuid import UUID

import httpx
import yaml
from sqlalchemy import func, select

from app.config import Settings
from app.demo.clock import install_timestamp_listener, uninstall_timestamp_listener, virtual_time
from app.demo.portfolio import DemoPortfolio
from app.demo.scenarios import DemoApplication, DemoPlan
from app.integrations.partner_notifier import PartnerNotifier
from app.models import Application, Decision, Employee, Guarantor
from app.ocr.fake_provider import FakeOcrProvider, build_ocr_result
from app.security.passwords import hash_password
from app.sources.bki import FakeBkiProvider, NotConfiguredBkiProvider
from app.sources.fssp import FakeFsspCaptchaSource
from app.storage.local import LocalDirectoryStorage

# --- демо-сотрудники -------------------------------------------------------------------

DEMO_EMPLOYEES = (
    ("admin", "demo.admin@example.com", "Администратор (демо)"),
    ("operator", "demo.operator@example.com", "Оператор (демо)"),
    ("viewer", "demo.viewer@example.com", "Наблюдатель (демо)"),
)

# Справочник товаров по ТЗ не знает автозапчастей и телефонов отдельно —
# заводим так же, как это сделал бы админ на экране «Справочники».
DEMO_GOODS_CATEGORIES = (
    {"code": "phones", "name": "Телефоны и смартфоны", "sharia_allowed": True, "liquidity": "high"},
    {"code": "auto_parts", "name": "Автозапчасти", "sharia_allowed": True, "liquidity": "medium"},
)


@dataclass(frozen=True)
class RulesetStep:
    days_ago: int
    at: time  # UTC
    comment: str
    # Отличия от config/rulesets/dev.yaml: путь через точку -> значение.
    # Последний шаг — без отличий: активная версия совпадает с файлом
    # (иначе «Состояние системы» показало бы расхождение с файлом).
    overrides: dict[str, Any]


RULESET_HISTORY = (
    RulesetStep(
        59, time(5, 30),
        "Старт пилота: порог автоодобрения 720 (осторожнее стартовых 700 из ТЗ, пока нет своей "
        "статистики); ПДН по КИ выше 55% — только на ручную проверку",
        {"score_thresholds.auto_approve_above": 720, "limits.pti_max_applies_to_credit_history": False},
    ),
    RulesetStep(
        36, time(7, 15),
        "Порог автоодобрения 720 → 700 (значение из ТЗ): за 3 недели пилота серая зона с баллом "
        "700–720 одобрялась вручную почти целиком",
        {"limits.pti_max_applies_to_credit_history": False},
    ),
    RulesetStep(
        14, time(5, 0),
        "Отказ по ПДН из КИ выше 60% (ТЗ, «Начальные значения»): раньше такие заявки уходили в серую зону",
        {},
    ),
)

_FSSP_CLEAN: list[dict[str, Any]] = []
_FSSP_NOISE = [{"subject": "Административный штраф ГИБДД", "amount": "1500.00",
                "raw": {"predmet": "Административный штраф ГИБДД", "summa": "1500.00"}}]
_FSSP_CREDIT_DEBT = [{"subject": "Задолженность по кредитному договору (банк)", "amount": "184300.00",
                      "raw": {"predmet": "Задолженность по кредитному договору (банк)", "summa": "184300.00"}}]


class SeedError(RuntimeError):
    pass


@dataclass
class SeedReport:
    logins: list[tuple[str, str]] = field(default_factory=list)  # (роль, email)
    statuses: dict[str, int] = field(default_factory=dict)
    mismatches: list[str] = field(default_factory=list)
    contracts: int = 0
    ruleset_versions: list[int] = field(default_factory=list)


class _Capture:
    """Подмена Celery-задачи: .delay(*args) складывает вызов в очередь сида."""

    def __init__(self, sink: list[tuple]) -> None:
        self._sink = sink

    def delay(self, *args) -> None:
        self._sink.append(args)


class _MemoryDownloader:
    """RegistryDownloader (app/registries/downloader.py) из готовых байтов —
    реестры грузятся штатными sync_* (атомарная замена + registry_sync_log)."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self._files = files

    async def download(self, registry: str) -> bytes:
        return self._files[registry]


def _check(response: httpx.Response, what: str) -> dict:
    # Правило 14 CLAUDE.md: ответ каждого вызова проверяется явно.
    if response.status_code >= 400:
        raise SeedError(f"{what}: HTTP {response.status_code} {response.text[:300]}")
    return response.json() if response.content else {}


def _set_path(data: dict, dotted: str, value: Any) -> None:
    *parents, leaf = dotted.split(".")
    node = data
    for key in parents:
        node = node[key]
    node[leaf] = value


class DemoSeeder:
    def __init__(
        self, *, plan: DemoPlan, portfolio: DemoPortfolio, password: str, settings: Settings,
        session_factory, fake_partner_url: str, fake_partner_token: str,
        log: Callable[[str], None] = print,
    ) -> None:
        self._plan = plan
        self._portfolio = portfolio
        self._password = password
        self._settings = settings
        self._session_factory = session_factory
        self._log = log
        self._fake_partner = httpx.AsyncClient(
            base_url=fake_partner_url.rstrip("/"), timeout=30.0,
            headers={"Authorization": f"Bearer {fake_partner_token}"},
        )
        self._notifier = PartnerNotifier(
            settings.partner_outbound_url, settings.partner_outbound_token,
            timeout_seconds=settings.partner_outbound_timeout_seconds,
        )
        self._storage = LocalDirectoryStorage(settings.storage_dir)
        self._stage_a_queue: list[tuple] = []
        self._stage_b_queue: list[tuple] = []
        self._fns_queue: list[tuple] = []
        self._fssp_source: FakeFsspCaptchaSource | None = None
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._sessions: dict[str, tuple[datetime, datetime]] = {}  # роль -> (вход, последний запрос)
        self._application_ids: dict[str, UUID] = {}
        self._webhook = None
        self._report = SeedReport()

    # --- инфраструктура ------------------------------------------------------------

    def _asgi_client(self, **kwargs) -> httpx.AsyncClient:
        from app.main import app as asgi_app

        # https — сессионная кука может быть Secure (как в tests/conftest.py::admin_client).
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=asgi_app), base_url="https://seed-demo", timeout=60.0, **kwargs
        )

    async def _as(self, role: str) -> httpx.AsyncClient:
        """Клиент админки под демо-сотрудником роли. Вход — заново, если
        сессия по виртуальным часам уже истекла бы: простой дольше
        session_idle_timeout_minutes или старше session_ttl_hours."""
        now = datetime.now(timezone.utc)
        client = self._clients.get(role)
        if client is None:
            client = self._asgi_client(headers={self._settings.csrf_header_name: "XMLHttpRequest"})
            self._clients[role] = client
        logged_in, last_used = self._sessions.get(role, (None, None))
        idle_limit = timedelta(minutes=self._settings.session_idle_timeout_minutes - 5)
        ttl_limit = timedelta(hours=self._settings.session_ttl_hours - 1)
        if logged_in is None or now - last_used > idle_limit or now - logged_in > ttl_limit or now < last_used:
            email = next(e for r, e, _ in DEMO_EMPLOYEES if r == role)
            _check(await client.post("/api/auth/login", json={"email": email, "password": self._password}),
                   f"вход {role}")
            logged_in = now
        self._sessions[role] = (logged_in, now)
        return client

    def _patches(self) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(mock.patch("app.tasks.stage_a.process_single_application_task",
                                       _Capture(self._stage_a_queue)))
        stack.enter_context(mock.patch("app.tasks.stage_b.process_single_application_task",
                                       _Capture(self._stage_b_queue)))
        stack.enter_context(mock.patch("app.tasks.fns_checks.run_fns_checks_task", _Capture(self._fns_queue)))
        stack.enter_context(mock.patch("app.api.fssp.build_fssp_captcha_source", lambda settings: self._fssp_source))
        return stack

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        if self._webhook is not None:
            await self._webhook.aclose()
        await self._fake_partner.aclose()
        await self._notifier.aclose()

    # --- шаги ------------------------------------------------------------------------

    async def _setup(self) -> None:
        """Начальная настройка «до пилота»: правила из файла (то же, что api
        при старте — на свежей базе версия 1 появится датой начала пилота),
        сотрудники, справочники, реестры."""
        from app.engine.versions import sync_ruleset_from_file

        await sync_ruleset_from_file(self._session_factory, self._settings.ruleset_path)

        admin_role, admin_email, admin_name = DEMO_EMPLOYEES[0]
        # Первый админ — так же, как app.cli.create_admin (консольная команда,
        # не эндпоинт), остальные — через API админки.
        async with self._session_factory() as session:
            session.add(Employee(full_name=admin_name, email=admin_email, role=admin_role,
                                 password_hash=hash_password(self._password)))
            await session.commit()
        self._report.logins.append((admin_role, admin_email))

        admin = await self._as("admin")
        for role, email, full_name in DEMO_EMPLOYEES[1:]:
            _check(await admin.post("/api/employees", json={
                "full_name": full_name, "email": email, "role": role, "password": self._password,
            }), f"сотрудник {role}")
            self._report.logins.append((role, email))

        for category in DEMO_GOODS_CATEGORIES:
            _check(await admin.post("/api/references/goods-categories", json=category), "категория товара")
        for row in self._plan.blacklist_rows:
            _check(await admin.post("/api/references/blacklist", json=row), "чёрный список")

        await self._load_registries()

    async def _load_registries(self) -> None:
        from app.registries.jobs import sync_invalid_passports, sync_sanctions_list

        passports = "passport_series,passport_number\n" + "".join(
            f"{s},{n}\n" for s, n in self._plan.invalid_passports
        )
        sanctions = "list_type,full_name,birth_date\n" + "".join(
            f"{r['list_type']},{r['full_name']},{r['birth_date']}\n" for r in self._plan.sanctions_rows
        )
        downloader = _MemoryDownloader({
            "mvd_invalid_passports": passports.encode("utf-8"),
            "rosfinmon_sanctions": sanctions.encode("utf-8"),
        })
        await sync_invalid_passports(self._session_factory, downloader)
        await sync_sanctions_list(self._session_factory, downloader)

    async def _publish_ruleset(self, step: RulesetStep) -> None:
        config = yaml.safe_load(Path(self._settings.ruleset_path).read_text(encoding="utf-8"))
        for dotted, value in step.overrides.items():
            _set_path(config, dotted, value)
        config["comment"] = step.comment
        config["effective_from"] = datetime.now(timezone.utc).date().isoformat()
        yaml_text = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)

        risk = await self._as("admin")  # T23: правила меняет admin
        simulation = _check(await risk.post("/api/rulesets/simulate", json={"yaml": yaml_text}), "симуляция")
        saved = _check(await risk.post("/api/rulesets/save", json={
            "yaml": yaml_text, "simulation_id": simulation["simulation_id"], "comment": step.comment,
        }), "публикация правил")
        self._report.ruleset_versions.append(saved["version"])

    async def _new_application(self, app: DemoApplication) -> None:
        person = app.applicant
        passport_fields = {k: v for k, v in person.ocr_fields().items()
                           if k not in ("last_name", "first_name", "middle_name", "birth_date", "birth_place")}
        composed = _check(await self._fake_partner.post("/_test/compose-new-application", json={
            "external_id": app.external_id, "last_name": person.last_name, "first_name": person.first_name,
            "middle_name": person.middle_name, "phone": person.phone, "birth_date": person.birth_date.isoformat(),
            "passport": passport_fields,
        }), "fake_partner compose-new-application")
        _check(await self._webhook.post("/api/v1/partner/applications", json=composed["body"]), "вебхук заявки")
        (application_id,) = self._stage_a_queue.pop()
        self._application_ids[app.external_id] = UUID(application_id)

    async def _stage_a(self, app: DemoApplication) -> None:
        from app.engine.pipeline import run_stage_a

        overrides = app.applicant.ocr_fields()
        result = (
            build_ocr_result(overrides=overrides, confidence_overrides={"passport_number": 0.52, "birth_date": 0.61})
            if app.ocr_low_confidence else build_ocr_result(overrides=overrides, confidence=0.97)
        )
        await run_stage_a(
            self._session_factory, self._application_ids[app.external_id],
            ocr_provider=FakeOcrProvider(result), partner_client=self._notifier, storage=self._storage,
        )
        await self._drain_fns(after=timedelta(seconds=50))

    async def _drain_fns(self, *, after: timedelta) -> None:
        """ФНС — фоновая задача (не блокирует стадию): результат приходит
        чуть позже решения, как у настоящего воркера."""
        from app.tasks.fns_checks import _run as run_fns

        jobs, self._fns_queue[:] = list(self._fns_queue), []
        if not jobs:
            return
        with virtual_time(datetime.now(timezone.utc) + after):
            for args in jobs:
                await run_fns(*args)

    async def _fssp(self, app: DemoApplication, *, subject: str, productions: list, reason: str | None = None) -> None:
        application_id = self._application_ids[app.external_id]
        subject_id = None
        region = app.applicant.fssp_region
        if subject == "guarantor":
            async with self._session_factory() as session:
                guarantor = (await session.execute(
                    select(Guarantor).where(Guarantor.application_id == application_id)
                )).scalars().first()
            if guarantor is None:
                raise SeedError(f"{app.external_id}: поручитель не найден")
            subject_id = str(guarantor.id)
            region = app.guarantor.fssp_region

        self._fssp_source = FakeFsspCaptchaSource(productions=productions)
        operator = await self._as("operator")
        started = _check(await operator.post(f"/api/applications/{application_id}/fssp/search", json={
            "subject_type": subject, "subject_id": subject_id, "region_code": region, "reason": reason,
        }), "ФССП: поиск")
        submitted = _check(await operator.post(
            f"/api/applications/{application_id}/fssp/{started['session_id']}/submit-code", json={"code": "000000"},
        ), "ФССП: код")
        if submitted.get("outcome") != "ok":
            raise SeedError(f"{app.external_id}: ФССП вернул {submitted.get('outcome')}")

    async def _review(self, app: DemoApplication, *, partner_status: str = "on_review") -> None:
        from app.engine.stage_b_pipeline import run_stage_b

        deal = app.deal
        composed = _check(await self._fake_partner.post(f"/_test/compose-review/{app.external_id}", json={
            "status": partner_status, "goods_category_code": deal.goods.category_code,
            "goods_name": deal.goods.name, "goods_price": float(deal.price),
            "down_payment": float(deal.down_payment), "term_months": deal.term_months, "tariff": deal.tariff,
            "monthly_payment": float(deal.monthly_payment), "employment_type": app.employment_type,
            "employer": app.employer, "position": app.position, "contacts": app.contacts,
            "declared_monthly_income": float(app.declared_income),
            "declared_monthly_obligations": float(app.declared_obligations), "bki_consent": True,
            "guarantor": app.guarantor.guarantor_payload() if app.guarantor else None,
        }), "fake_partner compose-review")
        body = composed["body"]
        # Согласие на КИ дано в момент звонка — по виртуальным часам (срок
        # согласия проверяется стадией Б), а не временем сборки тела.
        body["bki_consent"]["at"] = datetime.now(timezone.utc).isoformat()
        _check(await self._webhook.post("/api/v1/partner/review", json=body), "вебхук /review")
        (application_id,) = self._stage_b_queue.pop()

        person = app.applicant
        bki_provider = (
            FakeBkiProvider(reports={
                f"{person.last_name}|{person.first_name}|{person.birth_date.isoformat()}": app.bki_report,
            })
            if app.bki_configured else NotConfiguredBkiProvider()
        )

        override = app.partner_override_status if partner_status == "on_review" else None
        if override:
            reason = ("Клиент перезвонил, оператор оформил сам" if override == "approved"
                      else "Клиент отказался от покупки, оператор закрыл заявку")
            _check(await self._fake_partner.post("/_test/configure", json={
                "mode": "conflict", "conflict_current_status": override, "conflict_reason": reason,
            }), "fake_partner conflict")
        try:
            await run_stage_b(self._session_factory, UUID(application_id), partner_client=self._notifier,
                              bki_provider=bki_provider)
        finally:
            if override:
                _check(await self._fake_partner.post("/_test/configure", json={"mode": "ok"}), "fake_partner ok")
        await self._drain_fns(after=timedelta(seconds=40))

    async def _compliance(self, app: DemoApplication) -> None:
        application_id = self._application_ids[app.external_id]
        async with self._session_factory() as session:
            decision_id = (await session.execute(
                select(Decision.id).where(Decision.application_id == application_id, Decision.freeze.is_(True))
                .order_by(Decision.created_at.desc()).limit(1)
            )).scalar_one_or_none()
        if decision_id is None:
            raise SeedError(f"{app.external_id}: нет решения с заморозкой для комплаенса")
        comment = (
            "Однофамилец: в перечне нет даты рождения, по паспорту и месту рождения — другой человек"
            if app.compliance_verdict == "false_positive"
            else "Совпадение подтверждено: ФИО и дата рождения совпадают с перечнем"
        )
        compliance = await self._as("operator")  # T23: разбор совпадений — operator/admin
        _check(await compliance.post(f"/api/compliance/decisions/{decision_id}/mark-false-positive", json={
            "verdict": app.compliance_verdict, "comment": comment,
        }), "комплаенс")

    async def _import_portfolio(self) -> None:
        admin = await self._as("admin")
        today = datetime.now(timezone.utc).date().isoformat()
        uploads = (
            ("historical-applications", "demo_historical_applications.csv", self._portfolio.historical_csv, {}),
            ("contracts", "demo_contracts.csv", self._portfolio.contracts_csv, {"data_as_of": today}),
            ("payment-schedule", "demo_payment_schedule.csv", self._portfolio.schedule_csv, {}),
            ("payments", "demo_payments.csv", self._portfolio.payments_csv, {"data_as_of": today}),
        )
        for path, filename, content, form in uploads:
            result = _check(await admin.post(
                f"/api/imports/{path}", files={"file": (filename, content, "text/csv")}, data=form,
            ), f"импорт {path}")
            if result.get("errors"):
                raise SeedError(f"импорт {path}: {result['errors'][:3]}")

        from app.engine.delinquency_recompute import recompute_all_contracts

        async with self._session_factory() as session:
            await recompute_all_contracts(session)
            await session.commit()
        self._report.contracts = len(self._portfolio.contracts)

    # --- хронология ------------------------------------------------------------------

    def _events(self) -> list[tuple[datetime, int, str, Callable[[], Awaitable[None]]]]:
        counter = itertools.count()
        events: list[tuple[datetime, int, str, Callable[[], Awaitable[None]]]] = []

        def add(moment: datetime | None, label: str, action: Callable[[], Awaitable[None]]) -> None:
            if moment is not None:
                events.append((moment, next(counter), label, action))

        today = self._plan.now.date()
        for step in RULESET_HISTORY:
            add(datetime.combine(today - timedelta(days=step.days_ago), step.at, tzinfo=timezone.utc),
                f"правила: {step.comment[:40]}", lambda step=step: self._publish_ruleset(step))

        for app in self._plan.applications:
            add(app.submitted_at, f"{app.external_id} заявка", lambda a=app: self._new_application(a))
            add(app.stage_a_at, f"{app.external_id} стадия А", lambda a=app: self._stage_a(a))
            add(app.fssp_at, f"{app.external_id} ФССП", lambda a=app: self._fssp(
                a, subject="applicant",
                productions=_FSSP_NOISE if a.index % 4 == 0 else _FSSP_CLEAN,
            ))
            add(app.review_at, f"{app.external_id} /review", lambda a=app: self._review(a))
            add(app.guarantor_fssp_at, f"{app.external_id} ФССП поручителя",
                lambda a=app: self._fssp(a, subject="guarantor", productions=_FSSP_CLEAN))
            add(app.rereview_at, f"{app.external_id} повторное /review",
                lambda a=app: self._review(a, partner_status="manual_decision"))
            add(app.late_fssp_at, f"{app.external_id} ФССП после решения", lambda a=app: self._fssp(
                a, subject="applicant", productions=_FSSP_CREDIT_DEBT,
                reason="Повторная проверка перед выдачей товара" if a.fssp_applicant_before_review else None,
            ))
            add(app.compliance_at, f"{app.external_id} комплаенс", lambda a=app: self._compliance(a))

        heapq.heapify(events)
        return [heapq.heappop(events) for _ in range(len(events))]

    async def run(self) -> SeedReport:
        install_timestamp_listener()
        try:
            with self._patches():
                self._webhook = self._asgi_client(
                    headers={"Authorization": f"Bearer {self._settings.partner_inbound_token}"},
                )
                _check(await self._fake_partner.post("/_test/configure", json={"mode": "ok"}), "fake_partner ok")

                first = min(a.submitted_at for a in self._plan.applications)
                setup_at = datetime.combine(first.date() - timedelta(days=2), time(6, 0), tzinfo=timezone.utc)
                with virtual_time(setup_at):
                    await self._setup()
                self._log("настройка: сотрудники, справочники, реестры — готово")

                events = self._events()
                for n, (moment, _, label, action) in enumerate(events, start=1):
                    with virtual_time(moment):
                        try:
                            await action()
                        except SeedError:
                            raise
                        except Exception as exc:
                            raise SeedError(f"{label}: {exc.__class__.__name__}: {exc}") from exc
                    if n % 100 == 0:
                        self._log(f"событий выполнено: {n} из {len(events)}")

            # Портфель и свежие реестры — «сегодня», реальным временем.
            await self._import_portfolio()
            await self._load_registries()
            await self._verify()
            return self._report
        finally:
            uninstall_timestamp_listener()
            await self.aclose()

    async def _verify(self) -> None:
        async with self._session_factory() as session:
            rows = (await session.execute(
                select(Application.external_id, Application.status)
                .where(Application.external_id.like("DEMO-%"))
            )).all()
            statuses = dict(rows)
            counts = (await session.execute(
                select(Application.status, func.count()).group_by(Application.status)
            )).all()
        self._report.statuses = {status: count for status, count in counts}
        for app in self._plan.applications:
            actual = statuses.get(app.external_id)
            if actual != app.expected_status:
                self._report.mismatches.append(f"{app.external_id} ({app.kind}): ждали {app.expected_status}, "
                                               f"получили {actual}")


def contract_dates_summary(portfolio: DemoPortfolio) -> dict[str, int]:
    summary: dict[str, int] = {}
    for contract in portfolio.contracts:
        summary[contract.profile] = summary.get(contract.profile, 0) + 1
    return summary
