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
# bcrypt 라운드를 최소로 — 기본 12라운드는 한 번 해시에 약 0.27초가 걸리는데, 이 스위트는
# signup/login을 236곳에서 호출해 전체 시간의 대부분을 여기서 썼다(측정: 186초 중 ~100초).
# 테스트가 검증하는 건 해시 강도가 아니라 "맞는 비밀번호는 통과하고 틀린 건 막힌다"라
# 라운드 수와 무관하다. 운영 기본값(12)은 Settings에 그대로 있고 여기서만 덮는다.
os.environ["PASSWORD_HASH_ROUNDS"] = "4"
# 랭크 변동은 운영에서 꺼 두었지만(요청: 지금 구조가 깔끔하지 않아 일단 멈춘다) 기계 자체는
# 그대로 있다 — 여기서 켜 두어야 그 기계를 검사하는 테스트들이 계속 제 일을 한다. 꺼진 쪽
# 동작은 이 값을 그 테스트 안에서 되돌려 따로 본다(test_ranking_shift_disabled.py).
os.environ["RANKING_SHIFT_ENABLED"] = "true"

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.db.base import Base
from app.db.session import AsyncSessionLocal, engine

# 테이블 등록을 위해 모든 도메인 모델을 import 해야 한다.
from app.domain.app_version import models as _app_version_models  # noqa: F401
from app.domain.auth import models as _auth_models  # noqa: F401
from app.domain.challenges import models as _challenges_models  # noqa: F401
from app.domain.env_vars import models as _env_vars_models  # noqa: F401
from app.domain.activity import models as _feed_models  # noqa: F401
from app.domain.leagues import models as _leagues_models  # noqa: F401
from app.domain.game_results import models as _game_results_models  # noqa: F401
from app.domain.members import models as _members_models  # noqa: F401
# 일정(모임)이 뒤에 들어왔는데 여기 등록이 안 돼, 스키마를 만들 때 schedule_attendees가
# 빠져 스위트 전체가 setup에서 죽고 있었다 — 모델 하나를 더 부른다.
from app.domain.schedules import models as _schedules_models  # noqa: F401


# 스키마는 세션당 한 번만 만든다. 예전엔 테스트마다 drop_all + create_all로 통째로 다시
# 만들었는데, 테이블이 수십 개라 그 DDL이 테스트 하나당 0.3초 남짓 들어 스위트 시간의
# 큰 몫을 차지했다. 격리는 DDL이 아니라 아래 _reset_db가 행만 비우는 것으로 충분하다.
@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
async def _create_schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


# 테스트 사이 격리 — 자식(FK)부터 지우도록 의존 순서의 역순으로 비운다. SQLite는 테이블이
# 비면 rowid가 다시 1부터 시작하므로, 예전 drop/create처럼 매 테스트가 id=1부터 받는 전제도
# 그대로 유지된다(AUTOINCREMENT를 쓰는 테이블이 생기면 sqlite_sequence도 함께 비운다).
@pytest_asyncio.fixture(autouse=True)
async def _reset_db(_create_schema):
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
        # sqlite_sequence는 AUTOINCREMENT를 쓰는 테이블이 하나라도 있을 때만 생긴다.
        # 없는 상태에서 DELETE를 보내면 SQLite가 실행 전 파싱 단계에서 "no such table"로
        # 막으므로(조건절로 감싸도 소용없다) 존재 여부를 먼저 확인하고 보낸다.
        has_seq = await conn.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
        )
        if has_seq.first():
            await conn.exec_driver_sql("DELETE FROM sqlite_sequence")
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
