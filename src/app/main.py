from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import settings
from app.core.exceptions import (
    AppError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from app.core.logging import configure_logging

configure_logging()


async def _ensure_schema() -> None:
    """스키마가 없으면 생성한다 — 마이그레이션 없이 create_all 하나로 관리.

    기존 DB(테이블이 이미 있는 경우)에는 아무 것도 하지 않으므로 데이터가 보존된다.
    """
    # 모든 도메인 모델을 임포트해 Base.metadata에 테이블을 등록한다.
    from app.db.base import Base
    from app.db.session import engine
    from app.domain.app_version import models as _app_version_models  # noqa: F401
    from app.domain.auth import models as _auth_models  # noqa: F401
    from app.domain.challenges import models as _challenges_models  # noqa: F401
    from app.domain.env_vars import models as _env_vars_models  # noqa: F401
    from app.domain.feed import models as _feed_models  # noqa: F401
    from app.domain.match_requests import models as _match_requests_models  # noqa: F401
    from app.domain.matches import models as _matches_models  # noqa: F401
    from app.domain.members import models as _members_models  # noqa: F401

    async with engine.begin() as conn:
        # 랭킹변동(rank_shifts)은 스냅샷(rank_snapshots) 방식으로 대체됐다 — 옛 테이블은
        # 쌓인 데이터까지 함께 버린다(요청: 기존 랭킹변동 저장 테이블 드롭).
        from sqlalchemy import text
        await conn.execute(text("DROP TABLE IF EXISTS rank_shifts"))
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_match_notes(conn)
        await _add_match_result_summary(conn)
        await _drop_access_screen_code_check(conn)
        await _drop_legacy_match_notes(conn)
        await _drop_legacy_match_summary(conn)
    await _seed_rank_snapshots()


async def _add_match_result_summary(conn: object) -> None:
    """match_results.summary_data 컬럼을 더한다(멱등).

    스키마를 create_all로만 관리해(마이그레이션 없음) 이미 있는 테이블에는 새 컬럼이
    반영되지 않는다 — build_count 때와 같은 이유로 여기서 직접 ALTER 한다. IF NOT EXISTS는
    PostgreSQL/SQLite(3.35+) 모두 지원하고, 안 되는 환경이면 조용히 넘어간다.
    """
    import logging

    from sqlalchemy import text

    try:
        await conn.execute(  # type: ignore[attr-defined]
            text("ALTER TABLE match_results ADD COLUMN IF NOT EXISTS summary_data JSONB")
        )
    except Exception:  # noqa: BLE001 — 이미 있거나 미지원 DB면 그냥 넘어간다.
        logging.getLogger(__name__).debug("match_results.summary_data 컬럼 추가 건너뜀", exc_info=True)


async def _drop_access_screen_code_check(conn: object) -> None:
    """access_history의 낡은 화면코드 CHECK 제약을 떼어낸다(멱등).

    이 제약은 테이블이 처음 만들어질 때의 화면 목록으로 굳어 있는데, 스키마를 create_all로만
    관리해(마이그레이션 없음) 코드에서 목록을 고쳐도 기존 DB에는 영원히 반영되지 않았다 —
    그래서 새 화면(feed 등)의 접속 기록이 INSERT 단계에서 조용히 터졌다. 검증은 API 계층
    (schemas.ScreenCode)이 하므로 제약 자체를 없앤다. SQLite는 제약 삭제를 지원하지 않지만
    로컬/테스트는 새 DB로 만들어지면 이 제약이 아예 안 생기므로 문제되지 않는다.
    """
    import logging

    from sqlalchemy import text

    try:
        await conn.execute(  # type: ignore[attr-defined]
            text("ALTER TABLE access_history DROP CONSTRAINT IF EXISTS ck_access_history_screen_code")
        )
    except Exception:  # noqa: BLE001 — SQLite 등 미지원 DB는 그냥 넘어간다.
        logging.getLogger(__name__).debug("access_history 화면코드 제약 삭제 건너뜀", exc_info=True)


async def _seed_rank_snapshots() -> None:
    """rank_snapshots가 비어 있으면 현재 포인트·순위표를 기준선으로 1회 적재(멱등).

    다음 경기 등록/삭제가 이 기준선과 비교해 변동분을 만든다(요청: "최초 현 데이터
    쌓아주기"). 실패해도 부팅은 막지 않는다 — 첫 이벤트 때 기준 없이 전원 신규로 잡히는
    것 이상의 문제는 없다.
    """
    import logging

    from app.db.session import AsyncSessionLocal
    from app.domain.feed.service import RankSnapshotService
    from app.domain.matches.service import MatchService
    from app.storage import get_storage

    try:
        async with AsyncSessionLocal() as session:
            match_service = MatchService(session, get_storage())

            async def compute_entries(match_type: str, date_from: str, date_to: str):
                return await match_service.get_stats(
                    member_ids=None, date_from=date_from, date_to=date_to,
                    match_type=match_type, race=None,
                )

            await RankSnapshotService(session).seed_if_empty(compute_entries)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("랭크 스냅샷 기준선 적재 실패")


async def _migrate_match_notes(conn) -> None:
    """기존 경기 댓글(match_notes)을 일반화된 피드 댓글(feed_comments)로 1회 이관.

    feed_comments가 비어 있고 match_notes에 데이터가 있을 때만 복사한다(멱등).
    id를 그대로 보존해 언급(mentions) 매핑도 함께 옮긴다.

    이제 match_notes는 모델에서 빠져 create_all이 만들지 않는다 — 새로 만든 DB에는 아예
    없으므로, 테이블이 없으면 조용히 건너뛴다(이관할 것도 없다).
    """
    from sqlalchemy import inspect, text

    tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    if "match_notes" not in tables:
        return
    existing = await conn.scalar(text("SELECT COUNT(*) FROM feed_comments"))
    if existing and existing > 0:
        return
    legacy = await conn.scalar(text("SELECT COUNT(*) FROM match_notes"))
    if not legacy:
        return
    await conn.execute(text(
        "INSERT INTO feed_comments (id, target_type, target_id, text, created_at, updated_at, created_by, updated_by) "
        "SELECT id, 'match', match_id, text, created_at, updated_at, created_by, updated_by FROM match_notes"
    ))
    await conn.execute(text(
        "INSERT INTO feed_comment_mentions (comment_id, member_pk) "
        "SELECT note_id, member_pk FROM match_note_mentions"
    ))
    if conn.dialect.name == "postgresql":
        await conn.execute(text(
            "SELECT setval(pg_get_serial_sequence('feed_comments', 'id'), (SELECT MAX(id) FROM feed_comments))"
        ))
        await conn.execute(text(
            "SELECT setval(pg_get_serial_sequence('feed_comment_mentions', 'id'), "
            "(SELECT COALESCE(MAX(id), 1) FROM feed_comment_mentions))"
        ))



async def _drop_legacy_match_summary(conn) -> None:
    """옛 요약 문장 컬럼(match_results.summary TEXT)을 지운다.

    구조화된 summary_data로 갈아탄 뒤로는 코드 어디서도 읽지 않는다(6b7dc37) — 그때는
    "안 읽으면 그만"이라 물리 컬럼을 남겨 뒀지만, 이제 정리한다(요청). 옛 문장은 지금
    문구 규칙과 맞지 않아 되살릴 값이 아니고, 기존 경기는 리플레이를 다시 올려 채운다.

    컬럼이 없으면(새 DB) 아무것도 하지 않는다.
    """
    import logging

    from sqlalchemy import inspect, text

    def _has(sync_conn) -> bool:
        insp = inspect(sync_conn)
        if "match_results" not in insp.get_table_names():
            return False
        return any(c["name"] == "summary" for c in insp.get_columns("match_results"))

    try:
        if not await conn.run_sync(_has):
            return
        await conn.execute(text("ALTER TABLE match_results DROP COLUMN summary"))
        logging.getLogger(__name__).info("옛 요약 문장 컬럼(match_results.summary) 삭제 완료")
    except Exception:  # noqa: BLE001 — 미지원 DB(옛 SQLite 등)면 그냥 남겨 둔다.
        logging.getLogger(__name__).exception("match_results.summary 컬럼 삭제 실패")


async def _drop_legacy_match_notes(conn) -> None:
    """이관이 끝난 옛 경기 댓글 테이블을 지운다.

    바로 위 _migrate_match_notes가 같은 트랜잭션에서 먼저 돌아 남은 행을 feed_comments로
    옮긴 뒤라, 여기서 지우는 건 이미 복사된 것뿐이다. 그래도 되돌릴 수 없는 작업이므로
    '이관이 실제로 끝났다'는 증거를 한 번 더 확인한다 — match_notes에 있던 만큼이
    feed_comments에 들어와 있어야 한다. 하나라도 어긋나면 지우지 않고 그대로 둔다.

    자식(match_note_mentions)을 먼저 지워야 외래키가 걸리지 않는다.
    """
    import logging

    from sqlalchemy import inspect, text

    tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    if "match_notes" not in tables:
        return
    try:
        legacy = await conn.scalar(text("SELECT COUNT(*) FROM match_notes")) or 0
        moved = await conn.scalar(
            text("SELECT COUNT(*) FROM feed_comments WHERE target_type = 'match'")
        ) or 0
        if legacy > moved:
            logging.getLogger(__name__).warning(
                "match_notes(%s건)가 feed_comments(%s건)보다 많아 옛 테이블을 남겨 둔다", legacy, moved,
            )
            return
        await conn.execute(text("DROP TABLE IF EXISTS match_note_mentions"))
        await conn.execute(text("DROP TABLE IF EXISTS match_notes"))
        logging.getLogger(__name__).info("옛 경기 댓글 테이블 삭제 완료(%s건 이관 확인)", legacy)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("옛 경기 댓글 테이블 삭제 실패")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await _ensure_schema()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=_lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _register_exception_handlers(app)

    upload_root = Path(settings.storage_local_root)
    upload_root.mkdir(parents=True, exist_ok=True)
    # 랭킹 카톡 공유 기능 제거(요청)에 따른 잔여물 정리 — 공유 카드 썸네일이 쌓이던
    # share/ 하위를 부팅 시 비운다. 멱등이라 이미 비어 있으면 아무 일도 없다.
    import shutil
    shutil.rmtree(upload_root / "share", ignore_errors=True)
    app.mount(settings.storage_url_path, StaticFiles(directory=upload_root), name="uploads")

    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _register_exception_handlers(app: FastAPI) -> None:
    status_by_error = {
        NotFoundError: 404,
        ConflictError: 409,
        ValidationError: 400,
        UnauthorizedError: 401,
        ForbiddenError: 403,
    }

    for error_cls, status_code in status_by_error.items():

        def make_handler(code: int):
            async def handler(_request: Request, exc: AppError) -> JSONResponse:
                return JSONResponse(status_code=code, content={"detail": exc.message})

            return handler

        app.add_exception_handler(error_cls, make_handler(status_code))

    async def fallback_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": exc.message})

    app.add_exception_handler(AppError, fallback_handler)


app = create_app()
