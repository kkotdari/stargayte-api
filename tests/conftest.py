import os
from pathlib import Path

# 앱을 import 하기 전에 테스트용 환경변수를 설정한다 (SQLite 파일 DB 사용).
# DB URL 드라이버만 바꾸면 되므로, 운영 Postgres 코드 변경 없이 그대로 테스트에 활용한다.
# (in-memory sqlite는 커넥션마다 별도 DB가 생성돼 커넥션 풀과 충돌하므로 파일 DB를 사용한다.)
_TEST_DB_PATH = Path("var/test_uploads/test.db")
_TEST_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB_PATH}"
os.environ["JWT_SECRET_KEY"] = "test-secret"
os.environ["STORAGE_LOCAL_ROOT"] = "var/test_uploads"
os.environ["PUBLIC_BASE_URL"] = "http://testserver"

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.db.base import Base
from app.db.session import AsyncSessionLocal, engine

# 테이블 등록을 위해 모든 도메인 모델을 import 해야 한다.
from app.domain.app_version import models as _app_version_models  # noqa: F401
from app.domain.auth import models as _auth_models  # noqa: F401
from app.domain.challenges import models as _challenges_models  # noqa: F401
from app.domain.env_vars import models as _env_vars_models  # noqa: F401
from app.domain.match_requests import models as _match_requests_models  # noqa: F401
from app.domain.matches import models as _matches_models  # noqa: F401
from app.domain.members import models as _members_models  # noqa: F401
from app.domain.settings import models as _settings_models  # noqa: F401


@pytest_asyncio.fixture(autouse=True)
async def _reset_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def client():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest_asyncio.fixture
async def db_session():
    async with AsyncSessionLocal() as session:
        yield session
