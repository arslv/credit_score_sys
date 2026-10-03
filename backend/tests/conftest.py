import os
import asyncio
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

# Тесты всегда работают с отдельной базой (текущая БД + суффикс "_test"),
# а не с той, что задана для api/worker — чтобы не задеть данные приложения.
_base_db_url = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://dialog:dialog@localhost:5432/dialog"
)
_parts = urlsplit(_base_db_url)
_test_db_name = _parts.path.lstrip("/") + "_test"
os.environ["DATABASE_URL"] = _parts._replace(path=f"/{_test_db_name}").geturl()
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")

# Быстрые повторы для тестов адаптера интеграции — иначе реальный экспоненциальный
# backoff растянул бы тесты на секунды. lru_cache в get_settings() читает env один
# раз за процесс, поэтому значения нужно выставить до первого импорта app.config.
os.environ.setdefault("INTEGRATION_RETRY_MAX_ATTEMPTS", "3")
os.environ.setdefault("INTEGRATION_RETRY_BASE_DELAY_SECONDS", "0.05")
os.environ.setdefault("INTEGRATION_RETRY_MAX_DELAY_SECONDS", "0.2")

# То же самое для повторов распознавания паспорта (app/ocr/service.py) — иначе
# тест на недоступный провайдер ждал бы OCR_MAX_ATTEMPTS * OCR_RETRY_DELAY_SECONDS
# реального времени (по умолчанию в проде — 3 * 2с).
os.environ.setdefault("OCR_RETRY_DELAY_SECONDS", "0.01")

# APP_ENV по умолчанию — production (app/config.py, T11) — самый строгий режим,
# который отказывается стартовать с фейковыми провайдерами (app/startup_guard.py).
# Тесты сами используют FakeFnsSource/FakeBkiProvider/и т.п. повсеместно, поэтому
# без этого импорт app.main/app.celery_app (через app.tasks.*) падал бы уже на
# сборе тестов — до первого импорта app.config, как и остальные переменные выше.
os.environ.setdefault("APP_ENV", "test")

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest_asyncio.fixture(autouse=True)
async def _reset_redis_singleton():
    """app.redis.get_redis() кэширует клиент на весь процесс (как get_engine()
    в app.db) — но pytest-asyncio даёт каждому тесту свой event loop, и клиент,
    созданный в одном тесте, ловит "Event loop is closed" в следующем (T12,
    первый тест, реально использующий Redis из HTTP-эндпоинта, а не из
    Celery-задачи с одним asyncio.run() на процесс). Тот же фикс, что и для
    движка БД в app.db.new_sessionmaker — просто пересоздаём клиент на тест."""
    import app.redis as app_redis

    async def _drop():
        if app_redis._redis is not None:
            try:
                await app_redis._redis.aclose()
            except Exception:
                pass
            app_redis._redis = None

    await _drop()
    yield
    await _drop()


@pytest_asyncio.fixture(autouse=True)
async def _reset_db_singleton():
    """app.db.get_engine()/get_sessionmaker() кэшируют движок на весь процесс —
    это специально сделано для Celery-задач (app.db.new_sessionmaker() как раз
    создаёт СВОЙ движок в обход этого кэша, см. его docstring). Но HTTP-эндпоинты
    (T12, Depends(get_session)) используют именно закэшированный get_engine(), а
    pytest-asyncio даёт каждому тесту свой event loop: соединение из пула,
    открытое в одном тесте, ловит "attached to a different loop" в следующем,
    который через client/admin_client дойдёт до реальной БД (до T12 этого не
    происходило: единственный такой тест — test_health.py — был один на всю
    сессию). Тот же фикс, что и для Redis выше: пересоздаём движок на тест."""
    import app.db as app_db

    async def _drop():
        if app_db._engine is not None:
            try:
                await app_db._engine.dispose()
            except Exception:
                pass
            app_db._engine = None
            app_db._sessionmaker = None

    await _drop()
    yield
    await _drop()


def unique_valid_passport_series() -> str:
    """Случайная серия паспорта для тестов, где регион не важен, но должен
    получаться ВАЛИДНЫЙ год бланка (T08, app.engine.passport.compute_blank_year):
    цифры 3-4 фиксированы как "00" (год бланка 2000) — заведомо не в будущем
    вплоть до 2100 года, независимо от даты прогона тестов. Единственный общий
    генератор случайной серии в тестах — используется везде, где серия должна
    быть случайной (см. T08: без этого ~70% случайных серий давали год бланка
    в будущем и ложно срабатывал hard_stop PASSPORT_BLANK_YEAR_FUTURE раньше
    проверяемого в тесте правила)."""
    region = f"{uuid.uuid4().int % 100:02d}"
    return f"{region}00"


async def _ensure_test_database() -> None:
    from app.config import get_settings

    parts = urlsplit(get_settings().database_url.replace("+asyncpg", ""))
    db_name = parts.path.lstrip("/")

    conn = await asyncpg.connect(
        host=parts.hostname,
        port=parts.port or 5432,
        user=parts.username,
        password=parts.password,
        database="postgres",
    )
    try:
        # Каждый прогон тестов стартует с чистой базы: так проверка
        # "миграции применяются на чистой БД" остаётся честной раз за разом.
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            db_name,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def apply_migrations():
    asyncio.run(_ensure_test_database())
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        check=True,
    )
    yield


@pytest_asyncio.fixture
async def client():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def admin_client():
    """Как client, но https — сессионная кука ставится с Secure (T12), а httpx
    (как и настоящий браузер) не отправляет Secure-куку обратно на http-запрос,
    даже через ASGITransport без реальной сети."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def db_session():
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.config import get_settings

    # Собственный движок на тест: pytest-asyncio даёт каждому тесту свой event
    # loop, а закэшированный в app.db глобальный движок привязан к своему loop'у.
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            session = AsyncSession(bind=conn, expire_on_commit=False)
            try:
                yield session
            finally:
                await session.close()
                await trans.rollback()
    finally:
        await engine.dispose()


# Справочники — заполняются не тестами, а сидом/будущей миграцией (сейчас
# реальных сидов нет вообще, см. app/models/reference.py) — и не должны
# зависеть от того, какой тест выполнялся последним. Исключены из TRUNCATE
# ниже; правильность исключения проверяет
# test_session_isolation.py::test_truncate_mutable_tables_spares_reference_tables.
REFERENCE_TABLES = {"reason_codes", "goods_categories"}


async def truncate_mutable_tables(engine) -> None:
    """TRUNCATE всех таблиц приложения, кроме REFERENCE_TABLES. Вынесено в
    отдельную функцию (не инлайн в фикстуре), чтобы её можно было проверить
    напрямую одним самодостаточным тестом — не полагаясь на состояние в конце
    ВСЕЙ сессии pytest: там же есть test_migration_downgrades_and_upgrades_cleanly,
    который легитимно дропает и пересоздаёт всю схему (alembic downgrade base +
    upgrade head), и при случайном порядке (pytest-randomly) он может
    выполниться в любой момент — сентинел-строка, вставленная в начале сессии,
    это переживёт только случайно."""
    from sqlalchemy import text

    from app.models import Base

    table_names = ", ".join(
        f'"{t.name}"' for t in Base.metadata.sorted_tables if t.name not in REFERENCE_TABLES
    )
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))


@pytest_asyncio.fixture
async def session_factory():
    """Настоящий sessionmaker (без обёртки rollback) для кода, который сам
    управляет своими транзакциями — например, адаптер интеграции.

    После теста TRUNCATE всех таблиц приложения, кроме REFERENCE_TABLES (см.
    truncate_mutable_tables): commit одного теста иначе протекает в остальные
    до конца сессии pytest. Этот класс бага ловили трижды за одну рабочую
    сессию (T08: ruleset_versions дважды по разным причинам; T09: заморозка по
    санкциям на общем дефолтном паспорте) — каждый раз чинили точечно, в
    конкретном тесте. Это фикс на уровне фикстуры: избавляет от необходимости
    держать это в голове при каждом новом тесте на session_factory."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield maker
    finally:
        await truncate_mutable_tables(engine)
        await engine.dispose()


@pytest_asyncio.fixture
async def partner_admin():
    """Прямой httpx-клиент к fake_partner для служебных /_test/ эндпоинтов —
    не часть PartnerClient, только для подготовки данных в тестах.

    Сбрасывает store fake_partner перед каждым тестом (POST /_test/reset):
    сервис живёт весь docker compose, заявки в нём никогда не удаляются сами
    по себе, а list_applications там — O(N) по ВСЕМ когда-либо созданным
    заявкам (не только текущего теста). Без сброса N растёт с каждым
    прогоном pytest в рамках одного поднятого стека, и sync_applications
    (используется почти в каждом интеграционном тесте, дважды на вызов)
    заметно замедляется — это и есть причина деградации полного прогона с
    ~20 секунд до нескольких минут при повторных запусках без пересоздания
    контейнера (см. отчёт T11)."""
    import httpx

    from app.config import get_settings

    settings = get_settings()
    client = httpx.AsyncClient(
        base_url=settings.partner_base_url,
        headers={"Authorization": f"Bearer {settings.partner_token}"},
        timeout=10.0,
    )
    try:
        (await client.post("/_test/reset")).raise_for_status()
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def partner_client():
    from app.config import get_settings
    from app.integrations.fake_partner_client import FakePartnerClient

    settings = get_settings()
    client = FakePartnerClient(settings.partner_base_url, settings.partner_token)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def storage(tmp_path):
    from app.storage.local import LocalDirectoryStorage

    return LocalDirectoryStorage(str(tmp_path))
