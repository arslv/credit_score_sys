from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session


def new_sessionmaker() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Отдельный движок, не разделяемый с process-wide синглтоном get_engine().

    Нужен там, где код сам создаёт event loop на каждый вызов — например, Celery-задачи
    через asyncio.run(). asyncpg-соединения привязаны к тому loop'у, в котором были
    открыты; переиспользование закэшированного get_engine() между разными asyncio.run()
    ловит "Future attached to a different loop". Вызывающий код обязан вызвать
    engine.dispose() по завершении.
    """
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False)
