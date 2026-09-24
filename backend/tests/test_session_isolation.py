"""Проверяет саму фикстуру session_factory (T09, «Изоляция тестов»): TRUNCATE
после теста должен зачищать рабочие таблицы, но не трогать справочники
(REFERENCE_TABLES, conftest.py). Самодостаточный тест — не полагается на
состояние в конце всей сессии pytest, потому что в сессии есть
test_migration_downgrades_and_upgrades_cleanly (test_migrations.py), который
легитимно дропает и пересоздаёт всю схему, а при случайном порядке
(pytest-randomly) он может отработать в любой момент."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from tests.conftest import truncate_mutable_tables


async def test_truncate_mutable_tables_spares_reference_tables_but_clears_the_rest():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO reason_codes (code, kind, title, active) "
                    "VALUES ('__isolation_test__', 'reject', 'test sentinel', true)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO applications "
                    "(external_id, status, goods_name, goods_price, down_payment, term_months, "
                    "tariff, requested_amount, profile, consent_pd_at) "
                    "VALUES ('__isolation_test__', 'received', 'x', 1000, 0, 12, 'standard', "
                    "1000, '{}'::jsonb, now())"
                )
            )

        await truncate_mutable_tables(engine)

        async with engine.connect() as conn:
            reason_count = (
                await conn.execute(
                    text("SELECT count(*) FROM reason_codes WHERE code = '__isolation_test__'")
                )
            ).scalar_one()
            application_count = (
                await conn.execute(
                    text("SELECT count(*) FROM applications WHERE external_id = '__isolation_test__'")
                )
            ).scalar_one()
    finally:
        await engine.dispose()

    assert reason_count == 1, "TRUNCATE задел справочник reason_codes"
    assert application_count == 0, "TRUNCATE не зачистил рабочую таблицу applications"
