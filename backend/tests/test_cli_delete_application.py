"""Удаление тестовой заявки целиком (app/cli/delete_application.py, решение
владельца 24.11.2026): показ ничего не трогает, удаление убирает заявку и
всё, что на неё ссылается, соседняя заявка цела."""

from sqlalchemy import func, select

from app.cli import delete_application as cli
from app.engine.pipeline import run_stage_a
from app.models import Application, Check, Decision, StatusHistory
from app.ocr.fake_provider import FakeOcrProvider, build_ocr_result
from tests.test_engine_pipeline import _ingest, active_ruleset  # noqa: F401 — фикстура


async def _count(session_factory, model, application_id) -> int:
    async with session_factory() as session:
        stmt = select(func.count()).select_from(model).where(model.application_id == application_id)
        return (await session.execute(stmt)).scalar_one()


async def test_delete_application_dry_run_then_delete(
    partner_admin, partner_client, storage, session_factory, active_ruleset,
):
    doomed_ext, doomed_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    kept_ext, kept_id = await _ingest(partner_admin, partner_client, storage, session_factory)
    for application_id in (doomed_id, kept_id):
        await run_stage_a(
            session_factory, application_id, ocr_provider=FakeOcrProvider(build_ocr_result()),
            partner_client=partner_client, storage=storage,
        )
    assert await _count(session_factory, Check, doomed_id) > 0
    assert await _count(session_factory, StatusHistory, doomed_id) > 0

    # Показ: считает, ничего не удаляет.
    async with session_factory() as session:
        found = await cli.find_applications(session, [doomed_ext], [])
        assert [a.id for a in found] == [doomed_id]
        planned = dict(await cli.delete_applications(session, [doomed_id], dry_run=True))
        await session.rollback()
    assert planned["applications"] == 1
    assert planned["checks"] > 0
    assert await _count(session_factory, Check, doomed_id) > 0

    # Удаление.
    async with session_factory() as session:
        done = dict(await cli.delete_applications(session, [doomed_id], dry_run=False))
        await session.commit()
    assert done["applications"] == 1
    async with session_factory() as session:
        assert await session.get(Application, doomed_id) is None
        assert await session.get(Application, kept_id) is not None
    for model in (Check, Decision, StatusHistory):
        assert await _count(session_factory, model, doomed_id) == 0
    assert await _count(session_factory, Check, kept_id) > 0


async def test_cli_main_dry_run_by_default(partner_admin, partner_client, storage, session_factory, capsys):
    external_id, application_id = await _ingest(partner_admin, partner_client, storage, session_factory)

    assert await cli.main(["--external-id", external_id]) == 0
    out = capsys.readouterr().out
    assert "Это только показ" in out
    async with session_factory() as session:
        assert await session.get(Application, application_id) is not None

    assert await cli.main(["--external-id", external_id, "--yes"]) == 0
    async with session_factory() as session:
        assert await session.get(Application, application_id) is None

    assert await cli.main(["--external-id", "нет-такой"]) == 1
