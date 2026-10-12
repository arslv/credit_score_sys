"""T23: демо-данные — ~150 заявок за последние 60 дней через настоящий
конвейер, портфель договоров с платежами, история версий правил и
демо-аккаунты всех ролей (подробности — app/demo/).

Запуск (на ПУСТОЙ базе, worker и beat остановлены):
    docker compose stop worker beat
    docker compose exec api python -m app.cli.seed_demo --password '<пароль демо-аккаунтов>'
    docker compose start worker beat

Флага --reset нет (ваше решение): удалять строки из append-only таблиц
нельзя (правило 1 CLAUDE.md). Сброс — только целиком:
    docker compose down -v && docker compose up -d
    docker compose exec api alembic upgrade head
и снова seed_demo.

В APP_ENV=production отказывается запускаться: создаёт сотрудников с
паролем из командной строки и подменяет время."""

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.db import new_sessionmaker

RESET_HINT = (
    "Сбросьте базу: docker compose down -v, затем alembic upgrade head, затем seed_demo "
    "(docker compose up -d && docker compose exec api alembic upgrade head && "
    "docker compose exec api python -m app.cli.seed_demo --password ...)."
)
WORKER_HINT = (
    "Остановите worker и beat на время сида: docker compose stop worker beat — "
    "иначе они подхватят демо-заявки по расписанию с сегодняшним временем. "
    "После сида: docker compose start worker beat."
)
MIN_PASSWORD_LENGTH = 12  # то же, что app/cli/create_admin.py


class SeedRefused(Exception):
    pass


def check_environment(settings: Settings) -> None:
    if settings.app_env == "production":
        raise SeedRefused(
            "APP_ENV=production — демо-сид создаёт сотрудников с паролем из командной строки и "
            "подменяет время конвейера; в бою не запускается."
        )


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SeedRefused(f"Пароль демо-аккаунтов должен быть не короче {MIN_PASSWORD_LENGTH} символов.")


async def check_database_empty(session_factory) -> None:
    from app.demo.runner import DEMO_EMPLOYEES
    from app.models import Application, Employee

    async with session_factory() as session:
        applications = (await session.execute(select(func.count()).select_from(Application))).scalar_one()
        demo_emails = (await session.execute(
            select(func.count()).select_from(Employee).where(Employee.email.in_([e for _, e, _ in DEMO_EMPLOYEES]))
        )).scalar_one()
    if applications or demo_emails:
        raise SeedRefused(
            f"В базе уже есть данные (заявок: {applications}, демо-сотрудников: {demo_emails}) — "
            f"seed_demo работает только на пустой базе. {RESET_HINT}"
        )


def check_no_running_workers() -> None:
    from app.celery_app import celery_app

    try:
        replies = celery_app.control.ping(timeout=1.5)
    except Exception:  # брокер недоступен — воркеров заведомо нет
        return
    if replies:
        raise SeedRefused(f"Запущен Celery worker ({len(replies)}). {WORKER_HINT}")


def default_fake_partner_url(settings: Settings) -> str:
    parts = urlsplit(settings.partner_outbound_url)
    return f"{parts.scheme}://{parts.netloc}"


async def run_seed(
    *, password: str, seed: int, fake_partner_url: str, session_factory, settings: Settings,
    now: datetime | None = None, log=print,
):
    from app.demo.portfolio import build_portfolio
    from app.demo.runner import DemoSeeder
    from app.demo.scenarios import build_plan

    now = now or datetime.now(timezone.utc)
    plan = build_plan(now=now, seed=seed)
    portfolio = build_portfolio(plan, today=now.date(), seed=seed)
    seeder = DemoSeeder(
        plan=plan, portfolio=portfolio, password=password, settings=settings, session_factory=session_factory,
        fake_partner_url=fake_partner_url, fake_partner_token=settings.fake_partner_test_token, log=log,
    )
    return plan, portfolio, await seeder.run()


def _quiet_logs() -> None:
    """Сид прогоняет сотни шагов конвейера — его info-логи (и каждый HTTP-вызов
    httpx) заглушили бы итоговый отчёт. app.main при импорте сам настраивает
    логи, поэтому импорт — до перенастройки."""
    import logging

    import structlog

    import app.main  # noqa: F401

    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def _main(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        check_environment(settings)
        check_password(args.password)
        engine, session_factory = new_sessionmaker()
        try:
            await check_database_empty(session_factory)
            check_no_running_workers()
            _quiet_logs()
            print("Демо-сид: ~150 заявок за 60 дней через конвейер, это займёт пару минут…")
            plan, portfolio, report = await run_seed(
                password=args.password, seed=args.seed,
                fake_partner_url=args.fake_partner_url or default_fake_partner_url(settings),
                session_factory=session_factory, settings=settings,
            )
        finally:
            await engine.dispose()
    except SeedRefused as exc:
        print(str(exc), file=sys.stderr)
        return 1

    from app.demo.runner import contract_dates_summary

    print()
    print(f"Заявок по статусам (вся база): {dict(sorted(report.statuses.items()))}")
    print(f"Договоров: {report.contracts} — {contract_dates_summary(portfolio)}")
    print(f"Опубликованы версии правил: {', '.join(f'v{v}' for v in report.ruleset_versions)}")
    print()
    print("Демо-аккаунты (пароль — переданный в --password):")
    for role, email in report.logins:
        print(f"  {role:<13} {email}")
    if report.mismatches:
        print(f"\nРасхождения с планом ({len(report.mismatches)}):", file=sys.stderr)
        for line in report.mismatches:
            print(f"  {line}", file=sys.stderr)
        return 2
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Демо-данные DIALOG Scoring (T23)")
    parser.add_argument("--password", required=True, help="пароль всех демо-аккаунтов (не короче 12 символов)")
    parser.add_argument("--seed", type=int, default=2026, help="seed генератора (тот же seed — те же данные)")
    parser.add_argument("--fake-partner-url", default=None,
                        help="адрес fake_partner (по умолчанию — из PARTNER_OUTBOUND_URL)")
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
