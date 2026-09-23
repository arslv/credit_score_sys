from pathlib import Path

from sqlalchemy import select

from app.models import InvalidPassport, SanctionsList
from app.registries.jobs import sync_invalid_passports, sync_sanctions_list

FIXTURES = Path(__file__).parent / "fixtures"


class FixtureDownloader:
    """Отдаёт маленькие синтетические CSV из tests/fixtures — не настоящие
    выгрузки реестров (см. T08)."""

    _FILES = {
        "mvd_invalid_passports": FIXTURES / "mvd_invalid_passports_sample.csv",
        "rosfinmon_sanctions": FIXTURES / "rosfinmon_sanctions_sample.csv",
    }

    async def download(self, registry: str) -> bytes:
        return self._FILES[registry].read_bytes()


async def test_sync_invalid_passports_from_fixture_file(session_factory):
    log = await sync_invalid_passports(session_factory, FixtureDownloader())

    assert log.status == "ok"
    assert log.rows_loaded == 3

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(InvalidPassport).where(InvalidPassport.passport_series == "0001")
            )
        ).scalars().all()
        assert {r.passport_number for r in rows} == {"000001", "000002"}


async def test_sync_sanctions_list_from_fixture_file(session_factory):
    log = await sync_sanctions_list(session_factory, FixtureDownloader())

    assert log.status == "ok"
    assert log.rows_loaded == 3

    async with session_factory() as session:
        entry = (
            await session.execute(
                select(SanctionsList).where(SanctionsList.full_name == "Тестов Тест Тестович")
            )
        ).scalar_one()
        assert entry.list_type == "te"
        assert entry.birth_date is not None

        # birth_date может быть пустым в файле (последняя строка фикстуры).
        entry_no_birth_date = (
            await session.execute(
                select(SanctionsList).where(SanctionsList.full_name == "Вымышленный Вымысел Вымышленович")
            )
        ).scalar_one()
        assert entry_no_birth_date.birth_date is None
