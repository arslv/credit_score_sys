from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Response, status
from sqlalchemy import text

from app.config import get_settings
from app.db import get_engine, get_sessionmaker
from app.engine.schema import load_ruleset_from_yaml
from app.engine.versions import compute_config_hash, get_active_ruleset_version, load_config, sync_ruleset_from_file
from app.logging import configure_logging
from app.redis import get_redis
from app.registries.sync import is_registry_stale

configure_logging()
logger = structlog.get_logger()

_REGISTRIES = ("mvd_invalid_passports", "rosfinmon_sanctions")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    try:
        result = await sync_ruleset_from_file(get_sessionmaker(), settings.ruleset_path)
        logger.info("ruleset_startup_sync", result=result.value)
    except Exception:
        # БД может быть ещё не готова (миграции идут отдельным шагом compose-команды) —
        # не роняем старт приложения из-за этого, /health и pipeline всё равно
        # честно сообщат об отсутствии активного конфига.
        logger.exception("ruleset_startup_sync_failed")
    yield


app = FastAPI(title="DIALOG Scoring", lifespan=lifespan)


@app.get("/health")
async def health(response: Response) -> dict:
    """Помимо БД/Redis — свежесть локальных реестров и совпадение активного
    конфига правил с файлом на диске (T08). Ни то, ни другое не роняет сервис
    само по себе (устаревший реестр — не автоотказ, расхождение конфига — не
    повод падать, раз в БД уже есть рабочая версия), но должно быть видно
    снаружи без захода в базу: реальный алерт (почта/чат/пейджинг) настраивается
    мониторингом, который опрашивает этот эндпоинт и реагирует на status != "ok" —
    самих учётных данных для такого канала в проекте пока нет."""
    checks = {"database": "error", "redis": "error"}
    healthy = True

    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        healthy = False
        logger.error("health_check_failed", component="database")

    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception:
        healthy = False
        logger.error("health_check_failed", component="redis")

    registries: dict[str, str] = {r: "unknown" for r in _REGISTRIES}
    ruleset_info: dict = {"active_version": None, "matches_file": None}
    degraded = False

    if healthy:  # без БД ни то, ни другое всё равно не проверить
        try:
            settings = get_settings()
            session_factory = get_sessionmaker()
            async with session_factory() as session:
                ruleset_row = await get_active_ruleset_version(session)

            if ruleset_row is not None:
                ruleset_info["active_version"] = ruleset_row.version
                try:
                    file_config = load_ruleset_from_yaml(settings.ruleset_path)
                    ruleset_info["matches_file"] = compute_config_hash(file_config) == ruleset_row.config_hash
                except Exception:
                    ruleset_info["matches_file"] = False
                if not ruleset_info["matches_file"]:
                    degraded = True
                    logger.warning("ruleset_mismatch", active_version=ruleset_row.version)

                max_staleness_days = load_config(ruleset_row).sources.registries.max_staleness_days
                for registry in _REGISTRIES:
                    stale = await is_registry_stale(
                        session_factory, registry=registry, max_staleness_days=max_staleness_days
                    )
                    registries[registry] = "stale" if stale else "ok"
                    if stale:
                        degraded = True
                        logger.warning("registry_stale", registry=registry)
            else:
                degraded = True
        except Exception:
            logger.error("health_check_failed", component="ruleset_or_registries")

    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        overall = "unavailable"
    elif degraded:
        overall = "degraded"
    else:
        overall = "ok"

    return {"status": overall, "checks": checks, "registries": registries, "ruleset": ruleset_info}
