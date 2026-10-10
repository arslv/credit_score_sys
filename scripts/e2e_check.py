#!/usr/bin/env python3
"""Сквозная проверка DIALOG Scoring через настоящие Celery-задачи и заглушку
заказчика (fake_partner) — не вызовами app.engine.* функций напрямую.

T18: интеграция по новой схеме (push) — fake_partner теперь САМА шлёт нам
вебхуки (.../api/v1/partner/applications, .../review), имитируя заказчика, и
принимает наши исходящие уведомления на /partner/webhook. Опроса их API
(polling) больше нет — скрипт просит fake_partner запустить вебхук
(/_test/trigger-*) и смотрит, что мы ей потом отправили (/_test/received).

Как это работает:
- Заявки создаются через fake_partner (порт 8100, host) — скрипт просит её
  POST .../api/v1/partner/applications нам, как сделал бы их сайт/CRM, затем
  только НАБЛЮДАЕТ (/_test/received) и дожидается, пока обработку сделают
  фоновые задачи.
- "Фоновые задачи" — это те же самые функции, что зарегистрированы через
  @celery_app.task (app/tasks/*.py): process_new_applications_task,
  process_reviewed_applications_task, sync_applications_task. Скрипт вызывает
  их внутри контейнера api (docker compose exec, RUNNER_SERVICE), тем же
  кодом, которым их вызвал бы брокер по расписанию (app/celery_app.py) —
  просто без ожидания таймера, а по требованию скрипта, тик за тиком, пока
  заявка не дойдёт до терминального состояния или не истечёт таймаут. beat и
  worker на время прогона остановлены целиком (см. main()) — иначе задача,
  которую beat успел поставить в очередь Redis до остановки, всё равно
  досталась бы worker'у и гонялась бы конкурентно с нашими вызовами тем же
  дефолтным (не фикстурным) окружением над той же заявкой.
- Распознавание паспорта и БКИ — фейковые: OCR_PROVIDER_MODE=fixture (читает
  JSON-фикстуру, app/ocr/fixture_provider.py) и BKI_PROVIDER_MODE=fake
  (app/sources/bki.FakeBkiProvider, отчёты из JSON-фикстуры). Сценарий
  выбирается заявкой через то, какая фикстура лежит в контейнере api на
  момент конкретного тика — между сценариями скрипт её переписывает. Сами
  ФИО/фото, которые fake_partner шлёт нам вебхуком, на выбор сценария не
  влияют — решение целиком определяется содержимым фикстуры OCR/БКИ.
- Операции с БД (проверка итогового решения, посев санкционного перечня для
  сценария 2) идут через _e2e_runner.py — тот же принцип: не прямые вызовы
  Python-функций движка из ЭТОГО процесса, а отдельный вызов внутри
  контейнера, где реально живёт приложение и есть доступ к БД.

Запуск (из корня репозитория, при поднятом `docker compose up -d`):

    python3 scripts/e2e_check.py

Требует пересобранных api/worker/beat с последним кодом (docker compose build
api worker beat && docker compose up -d) — иначе фейковые провайдеры/фикстуры
из этой задачи (T11, "три вещи") в контейнере ещё не будет.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PARTNER_BASE_URL = "http://localhost:8100"  # fake_partner — её собственные /_test/* эндпоинты
PARTNER_TOKEN = "dev-token"  # см. FAKE_PARTNER_TOKEN в docker-compose.yml (дефолт для дев)
POLL_INTERVAL_SECONDS = 1.0
POLL_TIMEOUT_SECONDS = 30.0

OCR_FIXTURE_PATH = "/tmp/e2e_ocr.json"
BKI_FIXTURE_PATH = "/tmp/e2e_bki.json"

RUN = uuid.uuid4().hex[:8]  # уникальный суффикс на запуск скрипта — сценарии не пересекаются
RUN_DIGITS = f"{uuid.uuid4().int % 100000:05d}"  # только цифры — для passport_number (CHAR(6))


# --- низкоуровневые хелперы: docker compose, fake_partner HTTP -----------------


def _dc(*args: str, env: dict[str, str] | None = None, input_bytes: bytes | None = None) -> str:
    cmd = ["docker", "compose", "exec", "-T"]
    for key, value in (env or {}).items():
        cmd += ["-e", f"{key}={value}"]
    cmd += list(args)
    result = subprocess.run(
        cmd, cwd=REPO_ROOT, input=input_bytes, capture_output=True, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"docker compose exec failed: {' '.join(args)}\n"
            f"stdout: {result.stdout.decode(errors='replace')}\n"
            f"stderr: {result.stderr.decode(errors='replace')}"
        )
    return result.stdout.decode()


# Контейнер для наших собственных прямых вызовов _e2e_runner.py (не через
# Celery/Redis — обычный docker compose exec + python3). Раньше это был
# "worker", но на время прогона мы теперь останавливаем И beat, И worker
# целиком (найденный баг, T13c — см. main()), а docker compose exec требует
# ЖИВОЙ контейнер. "api" собран из того же ./backend с тем же кодом и тем же
# доступом к БД/Redis/fake_partner/storage_data, но его процесс — uvicorn, не
# celery worker, поэтому он не подбирает задачи из очереди сам и его можно
# оставить работающим всё время прогона.
RUNNER_SERVICE = "api"


# Раннер передаётся в контейнер текстом (python3 -c), а не копируется внутрь:
# копии в контейнере переживали правки и давали прогон на старом коде.
RUNNER_SOURCE = (REPO_ROOT / "scripts" / "_e2e_runner.py").read_text(encoding="utf-8")


def _write_fixture(path: str, content: dict) -> None:
    payload = json.dumps(content, ensure_ascii=False).encode("utf-8")
    _dc(RUNNER_SERVICE, "sh", "-c", f"cat > {path}", input_bytes=payload)


def run_worker_task(task: str, *, bki_mode: str | None = None) -> dict:
    """Вызывает НАСТОЯЩУЮ Celery-задачу (app/tasks/*.py) — тот же код, что
    вызвал бы celery beat по расписанию, но напрямую (без очереди), в
    контейнере RUNNER_SERVICE."""
    env = {"OCR_PROVIDER_MODE": "fixture", "OCR_FIXTURE_PATH": OCR_FIXTURE_PATH}
    if bki_mode == "fake":
        env["BKI_PROVIDER_MODE"] = "fake"
        env["BKI_FIXTURE_PATH"] = BKI_FIXTURE_PATH
    elif bki_mode == "not_configured":
        env["BKI_PROVIDER_MODE"] = "not_configured"
    out = _dc(
        RUNNER_SERVICE, "python3", "-c", RUNNER_SOURCE, "run_task",
        env=env, input_bytes=json.dumps({"task": task}).encode(),
    )
    return json.loads(out)


def runner_action(action: str, args: dict) -> dict:
    out = _dc(RUNNER_SERVICE, "python3", "-c", RUNNER_SOURCE, action, input_bytes=json.dumps(args).encode())
    return json.loads(out)


def _partner_request(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{PARTNER_BASE_URL}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {PARTNER_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else {"detail": exc.reason})


def partner_create_application(**overrides) -> str:
    """T18: просит fake_partner отправить нам POST .../api/v1/partner/applications
    (имитирует заказчика) — возвращает external_id, под которым заявка у нас
    появится."""
    status, body = _partner_request("POST", "/_test/trigger-new-application", overrides)
    if status != 200:
        raise RuntimeError(f"fake_partner trigger-new-application failed: {status} {body}")
    return body["external_id"]


def partner_received(external_id: str) -> dict:
    """Всё, что МЫ отправили по этой заявке на /partner/webhook, плюс
    current_status — статус, в который её перевёл наш последний POST
    (замена опросу GET /applications/{id} из polling-эры)."""
    status, body = _partner_request("GET", f"/_test/received/{external_id}")
    if status != 200:
        raise RuntimeError(f"fake_partner received lookup failed: {status} {body}")
    return body


def partner_status(external_id: str) -> str | None:
    return partner_received(external_id)["current_status"]


def partner_trigger_review(external_id: str, **overrides) -> None:
    """T18: просит fake_partner отправить нам POST .../api/v1/partner/review
    (имитирует «Принять на рассмотрение») — разумные дефолты уже заданы в
    fake_partner (TriggerReviewRequest: товар, доход, занятость, согласие на
    БКИ), overrides нужны только сценариям с особыми условиями."""
    status, body = _partner_request("POST", f"/_test/trigger-review/{external_id}", overrides)
    if status != 200:
        raise RuntimeError(f"fake_partner trigger-review failed: {status} {body}")


def partner_last_comment(external_id: str) -> str:
    items = [item for item in partner_received(external_id)["items"] if item["type"] == "decision"]
    return items[-1]["reason"] if items else ""


# --- сценарии --------------------------------------------------------------------


@dataclass
class ScenarioResult:
    name: str
    ok: bool
    path: list[str] = field(default_factory=list)
    decision: dict | None = None
    partner_status: str | None = None
    partner_comment: str = ""
    errors: list[str] = field(default_factory=list)


def _ocr_identity(last_name: str, first_name: str, birth_date: str, series: str, number: str) -> dict:
    return {
        "last_name": last_name, "first_name": first_name, "middle_name": "Тестович",
        "birth_date": birth_date, "passport_series": series, "passport_number": number,
    }


def _poll_until(check, *, timeout: float = POLL_TIMEOUT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(POLL_INTERVAL_SECONDS)
    return check()


def _wait_for_stage_a(external_id: str, bki_mode: str | None = None) -> None:
    """T18: заявка уже создана вебхуком (её ставит в очередь сам наш API,
    .delay()) — но worker/beat на время прогона остановлены (см. main()),
    поэтому тик за тиком вызываем ту же sweep-задачу напрямую, пока заявка
    не покинет 'new'/'received'."""
    def _done() -> bool:
        run_worker_task("stage_a")
        return partner_status(external_id) != "new"

    _poll_until(_done)


def _wait_for_stage_b(external_id: str, bki_mode: str) -> None:
    def _done() -> bool:
        run_worker_task("stage_b", bki_mode=bki_mode)
        status = partner_status(external_id)
        return status in ("rejected", "manual_decision", "approved")

    _poll_until(_done)


def scenario_good_client() -> ScenarioResult:
    name = "Хороший клиент"
    result = ScenarioResult(name=name, ok=True)
    try:
        last_name = f"Хорошев{RUN}"
        birth_date, series, number = "1988-04-12", "7700", f"1{RUN_DIGITS}"
        _write_fixture(
            OCR_FIXTURE_PATH, _ocr_identity(last_name, "Хороший", birth_date, series, number)
        )
        _write_fixture(
            BKI_FIXTURE_PATH,
            {
                f"{last_name}|Хороший|{birth_date}": {
                    "contracts": [], "inquiries": [], "score": 780,
                }
            },
        )

        external_id = partner_create_application(last_name=last_name, first_name="Хороший")
        result.path.append(f"created ({external_id})")

        _wait_for_stage_a(external_id)
        status_after_a = partner_status(external_id)
        result.path.append(f"stage_a -> partner:{status_after_a}")
        if status_after_a != "on_call":
            result.errors.append(f"после стадии А ожидался статус 'on_call', получили {status_after_a!r}")

        # T16: fssp_required_for_approval — approve недостижим без проверки
        # ФССП; капчу вводить некому в автоматическом прогоне (решает только
        # человек), поэтому результат пишем напрямую (см. _mark_fssp_checked).
        runner_action("mark_fssp_checked", {"external_id": external_id})

        partner_trigger_review(external_id)
        result.path.append("вебхук /review (заказчик «Принял на рассмотрение»)")

        _wait_for_stage_b(external_id, bki_mode="fake")
        result.partner_status = partner_status(external_id)
        result.partner_comment = partner_last_comment(external_id)
        result.path.append(f"stage_b -> partner:{result.partner_status}")

        state = runner_action("app_state", {"external_id": external_id})
        result.decision = state["decisions"][-1] if state["decisions"] else None

        if result.partner_status != "approved":
            result.errors.append(f"ожидался статус 'approved', получили {result.partner_status!r}")
        if not result.decision or result.decision["outcome"] != "approve":
            result.errors.append(f"ожидался Decision.outcome='approve', получили {result.decision}")
        if "Одобрено" not in result.partner_comment:
            result.errors.append(f"в комментарии нет «Одобрено»: {result.partner_comment!r}")
        if result.decision:
            amount = str(int(result.decision["approved_amount"]))
            term = str(result.decision["approved_term"])
            if amount not in result.partner_comment:
                result.errors.append(f"в комментарии нет суммы {amount}: {result.partner_comment!r}")
            if term not in result.partner_comment:
                result.errors.append(f"в комментарии нет срока {term}: {result.partner_comment!r}")
    except Exception as exc:  # noqa: BLE001 — сценарий должен упасть с понятной причиной, не трейсбеком
        result.errors.append(f"исключение: {exc}")

    result.ok = not result.errors
    return result


def scenario_sanctions_hit() -> ScenarioResult:
    name = "Совпадение по перечню"
    result = ScenarioResult(name=name, ok=True)
    try:
        last_name = f"Санкционов{RUN}"
        birth_date, series, number = "1985-06-15", "7800", f"2{RUN_DIGITS}"
        full_name = f"{last_name} Опасен Опасенович"
        _write_fixture(
            OCR_FIXTURE_PATH,
            {**_ocr_identity(last_name, "Опасен", birth_date, series, number), "middle_name": "Опасенович"},
        )

        seeded = runner_action(
            "seed_sanctions",
            {"full_name": full_name, "name_normalized": full_name.lower(), "birth_date": None},
        )
        if not seeded.get("ok"):
            result.errors.append(f"не удалось посеять SanctionsList: {seeded}")

        external_id = partner_create_application(last_name=last_name, first_name="Опасен")
        result.path.append(f"created ({external_id})")

        _wait_for_stage_a(external_id)
        result.partner_status = partner_status(external_id)
        result.partner_comment = partner_last_comment(external_id)
        result.path.append(f"stage_a -> partner:{result.partner_status}")

        state = runner_action("app_state", {"external_id": external_id})
        result.decision = state["decisions"][-1] if state["decisions"] else None
        checks = runner_action("checks_called", {"external_id": external_id})["sources"]
        result.path.append(f"checks вызваны: {checks}")

        if result.partner_status != "rejected":
            result.errors.append(f"ожидался статус 'rejected', получили {result.partner_status!r}")
        if not result.decision or result.decision["reason_code"] != "SANCTIONS_HIT":
            result.errors.append(f"ожидался reason_code='SANCTIONS_HIT', получили {result.decision}")
        if not result.decision or not result.decision["freeze"]:
            result.errors.append("заморозка (Decision.freeze) не сохранилась")
        if "комплаенс" not in result.partner_comment.lower():
            result.errors.append(f"в комментарии нет пометки для комплаенса: {result.partner_comment!r}")
        # T16: ФНС теперь fns_inn/fns_npd/fns_egrip (фоновая задача, не
        # вызывается синхронно при hard_stop), Федресурс убран целиком,
        # ФССП — только по ручному действию оператора (app/api/fssp.py).
        for external_source in ("fns_inn", "fns_npd", "fns_egrip", "fssp"):
            if external_source in checks:
                result.errors.append(f"внешний источник {external_source} был вызван — не должен был")
        if "rosfinmon" not in checks:
            result.errors.append("rosfinmon (источник совпадения) не значится среди вызванных проверок")
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"исключение: {exc}")

    result.ok = not result.errors
    return result


def scenario_bki_not_configured() -> ScenarioResult:
    name = "БКИ не подключено"
    result = ScenarioResult(name=name, ok=True)
    try:
        last_name = f"Обычнов{RUN}"
        birth_date, series, number = "1992-07-19", "7900", f"3{RUN_DIGITS}"
        _write_fixture(OCR_FIXTURE_PATH, _ocr_identity(last_name, "Обычный", birth_date, series, number))

        external_id = partner_create_application(last_name=last_name, first_name="Обычный")
        result.path.append(f"created ({external_id})")

        _wait_for_stage_a(external_id)
        result.path.append(f"stage_a -> partner:{partner_status(external_id)}")

        partner_trigger_review(external_id)
        result.path.append("вебхук /review (заказчик «Принял на рассмотрение»)")

        _wait_for_stage_b(external_id, bki_mode="not_configured")
        result.partner_status = partner_status(external_id)
        result.partner_comment = partner_last_comment(external_id)
        result.path.append(f"stage_b -> partner:{result.partner_status}")

        state = runner_action("app_state", {"external_id": external_id})
        result.decision = state["decisions"][-1] if state["decisions"] else None

        if result.partner_status != "manual_decision":
            result.errors.append(f"ожидался статус 'manual_decision', получили {result.partner_status!r}")
        if not result.decision or result.decision["reason_code"] != "BKI_NOT_CONFIGURED":
            result.errors.append(f"ожидался reason_code='BKI_NOT_CONFIGURED', получили {result.decision}")
        if "БКИ не подключено" not in result.partner_comment:
            result.errors.append(f"в комментарии нет пометки «БКИ не подключено»: {result.partner_comment!r}")
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"исключение: {exc}")

    result.ok = not result.errors
    return result


def scenario_credit_overload() -> ScenarioResult:
    name = "Перегруз по кредитной истории"
    result = ScenarioResult(name=name, ok=True)
    try:
        last_name = f"Перегрузов{RUN}"
        birth_date, series, number = "1985-11-03", "8000", f"4{RUN_DIGITS}"
        _write_fixture(OCR_FIXTURE_PATH, _ocr_identity(last_name, "Перегруз", birth_date, series, number))
        # Доход 100000, активный договор с платежом 70000 -> фактический ПДН 0.70,
        # выше soft_rules.pti_high (0.55, config/rulesets/dev.yaml) -> ручная проверка.
        _write_fixture(
            BKI_FIXTURE_PATH,
            {
                f"{last_name}|Перегруз|{birth_date}": {
                    "contracts": [
                        {
                            "opened_at": "2023-01-01", "closed_at": None, "status": "active",
                            "monthly_payment": "70000", "balance": "500000",
                            "overdue_days_max": 0, "subject": "Кредит наличными",
                        }
                    ],
                    "inquiries": [], "score": 720,
                }
            },
        )

        external_id = partner_create_application(last_name=last_name, first_name="Перегруз")
        result.path.append(f"created ({external_id})")

        _wait_for_stage_a(external_id)
        result.path.append(f"stage_a -> partner:{partner_status(external_id)}")

        partner_trigger_review(external_id)
        result.path.append("вебхук /review (заказчик «Принял на рассмотрение»)")

        _wait_for_stage_b(external_id, bki_mode="fake")
        result.partner_status = partner_status(external_id)
        result.partner_comment = partner_last_comment(external_id)
        result.path.append(f"stage_b -> partner:{result.partner_status}")

        state = runner_action("app_state", {"external_id": external_id})
        result.decision = state["decisions"][-1] if state["decisions"] else None

        if not result.decision or result.decision["outcome"] not in ("reject", "manual_review"):
            result.errors.append(f"ожидался reject или manual_review, получили {result.decision}")
        if result.decision and result.decision["outcome"] == "manual_review" and result.decision["reason_code"] != "PTI_HIGH":
            result.errors.append(f"manual_review ожидался по PTI_HIGH, получили {result.decision['reason_code']!r}")
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"исключение: {exc}")

    result.ok = not result.errors
    return result


def scenario_repeat_client_uses_cache() -> ScenarioResult:
    name = "Повторная заявка того же клиента (кэш БКИ)"
    result = ScenarioResult(name=name, ok=True)
    try:
        last_name = f"Повторнов{RUN}"
        birth_date, series, number = "1985-01-01", "8100", f"5{RUN_DIGITS}"
        _write_fixture(OCR_FIXTURE_PATH, _ocr_identity(last_name, "Повторный", birth_date, series, number))
        _write_fixture(
            BKI_FIXTURE_PATH,
            {f"{last_name}|Повторный|{birth_date}": {"contracts": [], "inquiries": [], "score": 760}},
        )

        first_id = partner_create_application(last_name=last_name, first_name="Повторный")
        _wait_for_stage_a(first_id)
        partner_trigger_review(first_id)
        _wait_for_stage_b(first_id, bki_mode="fake")
        result.path.append(f"первая заявка ({first_id}) -> partner:{partner_status(first_id)}")

        second_id = partner_create_application(last_name=last_name, first_name="Повторный")
        _wait_for_stage_a(second_id)
        partner_trigger_review(second_id)
        _wait_for_stage_b(second_id, bki_mode="fake")
        result.partner_status = partner_status(second_id)
        result.partner_comment = partner_last_comment(second_id)
        result.path.append(f"вторая заявка ({second_id}) -> partner:{result.partner_status}")

        state = runner_action("app_state", {"external_id": second_id})
        result.decision = state["decisions"][-1] if state["decisions"] else None

        attempts = runner_action("bki_attempt_count", {"external_id": second_id})
        result.path.append(f"платных попыток БКИ на клиента: {attempts['count']}")

        first_state = runner_action("app_state", {"external_id": first_id})
        if first_state["client_id"] != state["client_id"]:
            result.errors.append("две заявки не связались с одним и тем же клиентом (client_id различается)")
        if attempts["count"] != 1:
            result.errors.append(f"ожидалась ровно 1 платная попытка БКИ на двоих, получили {attempts['count']}")
        if not result.decision or result.decision["outcome"] not in ("approve", "manual_review", "reject"):
            result.errors.append(f"вторая заявка не получила решения: {result.decision}")
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"исключение: {exc}")

    result.ok = not result.errors
    return result


SCENARIOS = [
    scenario_good_client,
    scenario_sanctions_hit,
    scenario_bki_not_configured,
    scenario_credit_overload,
    scenario_repeat_client_uses_cache,
]


def main() -> int:
    print(f"=== DIALOG Scoring e2e-проверка (прогон {RUN}) ===\n")

    status, _ = _partner_request("GET", "/health")
    if status != 200:
        print("ОШИБКА: fake_partner недоступен на http://localhost:8100 — поднят ли docker compose?")
        return 2

    # Сброс заглушки перед прогоном: новый префикс external_id, без коллизий с
    # заявками из прошлых прогонов, уже лежащими в БД (приём идемпотентен по
    # external_id — повторная заявка молча не обрабатывается).
    status, _ = _partner_request("POST", "/_test/reset")
    if status != 204:
        print(f"ОШИБКА: не удалось сбросить fake_partner (HTTP {status})")
        return 2


    # celery beat в постоянно поднятом docker compose сам, по расписанию,
    # гоняет те же задачи — с ДЕФОЛТНЫМ окружением контейнера (реальный
    # AnthropicOcrProvider, пустой ключ). Без остановки beat его тик может
    # обогнать наш (с OCR_PROVIDER_MODE=fixture) и попытаться распознать фото
    # настоящим Anthropic без ключа — заявка застревает в статусе "ocr"
    # навсегда.
    #
    # Останавливать только beat недостаточно (найденный баг, T13c): beat —
    # это ПЛАНИРОВЩИК, он кладёт задачи в очередь Redis по расписанию, а
    # выполняет их отдельный, постоянно работающий контейнер `worker` — тот
    # же самый, где потом исполняются наши прямые вызовы _e2e_runner.py.
    # Задача, поставленная в очередь ПОСЛЕДНИМ тиком beat ДО его остановки,
    # никуда не девается — worker всё равно её разгребёт, тем же дефолтным
    # (не фикстурным) окружением, конкурентно с нашими вызовами над той же
    # только что созданной заявкой: реальный провайдер держит блокировку
    # строки заявки на время сетевого таймаута к Anthropic с пустым ключом,
    # наш собственный вызов stage_a эту же заявку взять не может — отсюда
    # статус "new" ровно 30 секунд (POLL_TIMEOUT_SECONDS) и потом 409 при
    # «Рассмотреть» (переход "new" -> "on_review" запрещён, потому что стадия
    # А фактически не отработала). Останавливаем оба контейнера — worker
    # прогону не нужен, все задачи и так исполняются напрямую в его образе
    # через _e2e_runner.py, без Celery/Redis.
    print("Останавливаю celery beat и worker на время прогона (иначе они гоняют "
          "задачи настоящим OCR-провайдером в фоне и мешают фикстурам)...")
    subprocess.run(
        ["docker", "compose", "stop", "beat", "worker"], cwd=REPO_ROOT, check=True, capture_output=True
    )

    try:
        results: list[ScenarioResult] = []
        for scenario_fn in SCENARIOS:
            result = scenario_fn()
            results.append(result)

            print(f"--- {result.name} ---")
            for step in result.path:
                print(f"  путь: {step}")
            if result.decision:
                print(
                    f"  решение: {result.decision['outcome']} (причина: {result.decision['reason_code']}), "
                    f"стадия {result.decision['stage']}"
                )
            print(f"  статус в fake_partner: {result.partner_status}")
            print(f"  комментарий: {result.partner_comment!r}")
            if result.ok:
                print("  ОК\n")
            else:
                print("  ОШИБКА:")
                for err in result.errors:
                    print(f"    - {err}")
                print()
    finally:
        print("Возвращаю celery beat и worker...")
        subprocess.run(
            ["docker", "compose", "start", "beat", "worker"], cwd=REPO_ROOT, check=True, capture_output=True
        )

    print("=== Сводка ===")
    for result in results:
        print(f"  [{'OK' if result.ok else 'FAIL'}] {result.name}")

    failed = [r for r in results if not r.ok]
    if failed:
        print(f"\n{len(failed)} из {len(results)} сценариев не прошли.")
        return 1

    print(f"\nВсе {len(results)} сценариев пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
