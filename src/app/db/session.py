from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# echo/pool 옵션은 개발 편의를 위한 것으로, DATABASE_URL 의 드라이버만 바꾸면
# Postgres 외 다른 SQLAlchemy 비동기 드라이버(MySQL, SQLite 등)로도 그대로 동작한다.
engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session
