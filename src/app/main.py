import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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
    from app.domain.game_results import models as _game_results_models  # noqa: F401
    from app.domain.members import models as _members_models  # noqa: F401

    async with engine.begin() as conn:
        # 랭킹변동(rank_shifts)은 스냅샷(rank_snapshots) 방식으로 대체됐다 — 옛 테이블은
        # 쌓인 데이터까지 함께 버린다(요청: 기존 랭킹변동 저장 테이블 드롭).
        from sqlalchemy import text
        await conn.execute(text("DROP TABLE IF EXISTS rank_shifts"))
        # 이름 정리(요청: 계층에 맞춰 통일)로 테이블 몇 개의 이름이 바뀌었다 — create_all보다
        # 반드시 먼저 돌려야 한다. 나중에 돌리면 create_all이 새 이름의 빈 테이블을 먼저
        # 만들어 버려서, 옛 테이블은 데이터를 안은 채 이름을 못 바꾸고 남는다.
        await _rename_legacy_tables(conn)
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_match_notes(conn)
        await _migrate_feed_target_types(conn)
        await _add_match_result_summary(conn)
        await _add_match_result_map_hash(conn)
        await _add_challenge_time_note(conn)
        await _drop_challenge_time(conn)
        await _drop_challenge_revenge_chain(conn)
        await _drop_access_screen_code_check(conn)
        await _drop_legacy_match_notes(conn)
        await _drop_legacy_match_summary(conn)
    await _seed_ranking_shifts()


# 이름 정리(요청) 이전에 쓰던 테이블 이름 → 지금 이름. 피드 1뎁스가 게임결과/너 나와/
# 랭크변동이고 게임결과 포스트 안의 2뎁스가 게임결과 카드라는 계층에 맞춘 것이다.
# match_requests(@지목 공개 요청글)는 이 셋과 별개 기능이라 손대지 않는다.
_TABLE_RENAMES = [
    ("matches", "game_results"),
    ("match_participants", "game_result_participants"),
    ("match_results", "game_outcomes"),
    ("rank_snapshots", "ranking_shifts"),
]


async def _rename_legacy_tables(conn) -> None:
    """옛 이름의 테이블을 지금 이름으로 바꾼다(멱등).

    옛 이름이 있고 새 이름이 아직 없을 때만 바꾼다 — 이미 바뀐 DB에서는 아무 일도 안 하고,
    둘 다 있는(사람이 손댔거나 create_all이 먼저 돈) 이상한 상태에서도 덮어쓰지 않는다.
    ALTER TABLE … RENAME TO는 PostgreSQL·SQLite 모두 지원하고, 두 DB 다 이 테이블을
    가리키는 외래키 참조를 자동으로 따라 고친다.

    컬럼 이름(match_id·match_no·match_type 등)은 그대로 둔다 — 물리 이름까지 바꾸면
    되돌리기가 훨씬 어려워지는데, 밖으로 나가는 이름은 어차피 ORM 속성과 API 스키마가
    정하므로 얻는 게 없다.
    """
    import logging

    from sqlalchemy import inspect, text

    have = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    for old, new in _TABLE_RENAMES:
        if old not in have or new in have:
            continue
        try:
            await conn.execute(text(f"ALTER TABLE {old} RENAME TO {new}"))
            logging.getLogger(__name__).info("테이블 이름 변경: %s -> %s", old, new)
        except Exception:  # noqa: BLE001 — 실패해도 부팅은 막지 않는다(옛 이름 그대로 남는다).
            logging.getLogger(__name__).exception("테이블 이름 변경 실패: %s -> %s", old, new)


async def _add_match_result_summary(conn: object) -> None:
    """game_outcomes.summary_data 컬럼을 더한다(멱등).

    스키마를 create_all로만 관리해(마이그레이션 없음) 이미 있는 테이블에는 새 컬럼이
    반영되지 않는다 — build_count 때와 같은 이유로 여기서 직접 ALTER 한다. IF NOT EXISTS는
    PostgreSQL/SQLite(3.35+) 모두 지원하고, 안 되는 환경이면 조용히 넘어간다.
    """
    import logging

    from sqlalchemy import text

    try:
        await conn.execute(  # type: ignore[attr-defined]
            text("ALTER TABLE game_outcomes ADD COLUMN IF NOT EXISTS summary_data JSONB")
        )
    except Exception:  # noqa: BLE001 — 이미 있거나 미지원 DB면 그냥 넘어간다.
        logging.getLogger(__name__).debug("game_outcomes.summary_data 컬럼 추가 건너뜀", exc_info=True)


async def _add_match_result_map_hash(conn: object) -> None:
    """game_outcomes.map_hash 컬럼을 더한다(멱등).

    위 _add_match_result_summary와 같은 이유 — create_all은 이미 있는 테이블에 새 컬럼을
    넣어주지 않는다. 미니맵 격자(replay_maps)를 가리키는 내용 해시다. 새로 만들어지는
    replay_maps 테이블 자체는 create_all이 알아서 만든다.
    """
    import logging

    from sqlalchemy import text

    try:
        await conn.execute(  # type: ignore[attr-defined]
            text("ALTER TABLE game_outcomes ADD COLUMN IF NOT EXISTS map_hash VARCHAR(64)")
        )
    except Exception:  # noqa: BLE001 — 이미 있거나 미지원 DB면 그냥 넘어간다.
        logging.getLogger(__name__).debug("game_outcomes.map_hash 컬럼 추가 건너뜀", exc_info=True)


async def _add_challenge_time_note(conn: object) -> None:
    """challenges.scheduled_time_note 컬럼을 더한다(멱등).

    위 _add_match_result_summary와 같은 이유 — create_all은 이미 있는 테이블에 새 컬럼을
    넣어주지 않는다. 시간을 사람 말로 적어 두는 자리다(models.py 주석 참고).
    """
    import logging

    from sqlalchemy import text

    try:
        await conn.execute(  # type: ignore[attr-defined]
            text("ALTER TABLE challenges ADD COLUMN IF NOT EXISTS scheduled_time_note TEXT NOT NULL DEFAULT ''")
        )
    except Exception:  # noqa: BLE001 — 이미 있거나 미지원 DB면 그냥 넘어간다.
        logging.getLogger(__name__).debug("challenges.scheduled_time_note 컬럼 추가 건너뜀", exc_info=True)


async def _drop_challenge_revenge_chain(conn: object) -> None:
    """challenges.reapplied_from_id(설욕전 체인 컬럼)를 지운다(멱등).

    "너 나와!"에서 설욕전(재대결) 개념 자체를 없앴다(요청) — 도전장끼리 이어 붙는 연계가
    사라졌으므로 원본을 가리키던 이 컬럼도 쓸 데가 없다.

    운영(PostgreSQL)은 컬럼을 지우면 딸린 외래키 제약도 함께 사라져 그대로 성공한다.
    SQLite는 외래키에 걸린 컬럼을 못 지운다고 거절하는데, 그래도 문제는 없다 — 로컬/테스트
    DB는 create_all로 새로 만들어지고 모델에서 이 컬럼이 빠졌으니 애초에 생기지 않는다.
    이미 컬럼이 있는 옛 SQLite 파일에만 값이 남고, 아무 코드도 그 값을 읽지 않는다.
    """
    import logging

    from sqlalchemy import inspect, text

    try:
        # IF EXISTS는 PostgreSQL만 받는다(SQLite는 DROP COLUMN까지만) — 있는지 먼저 보고
        # 조건 없는 DROP COLUMN을 날려 두 DB 모두에서 실제로 지워지게 한다.
        cols = await conn.run_sync(  # type: ignore[attr-defined]
            lambda c: {col["name"] for col in inspect(c).get_columns("challenges")}
        )
        if "reapplied_from_id" not in cols:
            return
        await conn.execute(text("ALTER TABLE challenges DROP COLUMN reapplied_from_id"))  # type: ignore[attr-defined]
        logging.getLogger(__name__).info("challenges.reapplied_from_id 삭제 완료")
    except Exception:  # noqa: BLE001 — 실패해도 부팅은 막지 않는다(컬럼이 그대로 남는다).
        logging.getLogger(__name__).warning(
            "challenges.reapplied_from_id 삭제 건너뜀(외래키에 걸린 컬럼을 못 지우는 DB)",
            exc_info=True,
        )


async def _drop_challenge_time(conn: object) -> None:
    """challenges.scheduled_time(옛 시각 컬럼)을 지운다(멱등).

    너 나와는 이제 날짜만 정하고 "언제"는 사람 말로 적는다(위 _add_challenge_time_note) —
    시각 컬럼은 쓰지 않게 됐고, 필수값도 아니었으므로 남은 값과 함께 통째로 버린다(요청).
    """
    import logging

    from sqlalchemy import text

    try:
        await conn.execute(  # type: ignore[attr-defined]
            text("ALTER TABLE challenges DROP COLUMN IF EXISTS scheduled_time")
        )
    except Exception:  # noqa: BLE001 — 이미 없거나 미지원 DB면 그냥 넘어간다.
        logging.getLogger(__name__).debug("challenges.scheduled_time 컬럼 삭제 건너뜀", exc_info=True)


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


async def _rank_entries_computer(session):
    """랭크 스냅샷 계산기 — 피드 도메인이 경기 통계를 되부르는 순환을 피해 콜백으로 넘긴다."""
    from app.domain.game_results.service import GameResultService
    from app.storage import get_storage

    game_result_service = GameResultService(session, get_storage())

    async def compute_entries(match_type: str, date_from: str, date_to: str):
        return await game_result_service.get_stats(
            member_ids=None, date_from=date_from, date_to=date_to,
            match_type=match_type, race=None,
        )

    return compute_entries


async def _seed_ranking_shifts() -> None:
    """rank_snapshots가 비어 있으면 현재 포인트·순위표를 기준선으로 1회 적재(멱등).

    변동분 없이(reason="seed") 저장되므로 피드에는 안 보인다. 실패해도 부팅은 막지 않는다.
    """
    import logging

    from app.db.session import AsyncSessionLocal
    from app.domain.feed.service import RankingShiftService

    try:
        async with AsyncSessionLocal() as session:
            await RankingShiftService(session).seed_if_empty(await _rank_entries_computer(session))
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("랭크 스냅샷 기준선 적재 실패")


# 하루 한 번 순위표를 다시 집계한다(요청) — 예전처럼 경기 등록/삭제마다 계산하면 하루에도
# 여러 번 변동 카드가 떠서 피드가 그 카드로 도배됐다. 하루치를 모아 한 번만 남긴다.
#
# 예전에는 "다음 자정까지 남은 초만큼 sleep" 하나로 만들어 뒀는데, 그러면 그 순간에 프로세스가
# 살아 있어야만 돈다 — 그리고 실제로 안 돌았다(지적). 이 앱은 새벽에 아무도 안 쓰니 그때
# 컨테이너가 잠들거나(무료/저트래픽 플랜) 배포·재시작으로 프로세스가 새로 뜨는 일이 잦고,
# 새로 뜨면 또 '다음 자정'을 기다리기 시작하므로 그 하루는 통째로 건너뛴다. 놓친 것을
# 알아채는 장치도 없었다.
#
# 그래서 '정확한 시각에 깨어나기'를 버리고 '밀린 일을 찾아 하기'로 바꿨다: 짧은 주기로 깨어나
# ① 오늘 목표 시각을 지났고 ② 오늘 남긴 스냅샷이 아직 없으면 그때 집계한다. 부팅 직후에도
# 같은 검사를 하므로, 목표 시각에 잠들어 있었더라도 그 뒤 처음 누가 앱을 열면 그때 돈다.
# 이 방식은 상태를 DB에서 읽으므로 재시작에 영향받지 않는다.
_RANK_RECOMPUTE_TZ = ZoneInfo("Asia/Seoul")
# 밀린 일이 있는지 확인하는 주기. 짧게 두는 건 잠에서 깬 직후를 빨리 잡기 위해서고, 확인
# 자체는 스냅샷 한 줄을 읽는 것뿐이라 부담이 없다.
_RANK_CHECK_INTERVAL_SEC = 10 * 60


def _rank_recompute_due(latest_at: datetime | None, last_try: date | None) -> bool:
    """지금 집계해야 하나 — 목표 시각을 지났고, 오늘 아직 안 남겼고, 이 프로세스에서 오늘
    시도한 적도 없을 때만.

    오늘 남긴 스냅샷이 있으면 건너뛰는 게 핵심이다. 아침에 한 번 돌고 낮에 경기가 등록된
    뒤 재시작이 걸리면, 그 검사가 없으면 같은 날 두 번째 변동 카드가 뜬다(하루에 카드
    하나라는 규칙이 깨진다).

    last_try(이 프로세스에서 마지막으로 시도한 날)까지 함께 보는 이유: 순위표가 그대로인
    날은 recompute_daily가 아무 행도 남기지 않으므로 DB만 보면 '아직 안 했다'로 계속
    읽힌다 — 그러면 10분마다 헛돌게 된다.
    """
    now = datetime.now(_RANK_RECOMPUTE_TZ)
    if now.hour < settings.rank_recompute_hour:
        return False
    if last_try == now.date():
        return False
    if latest_at is None:
        return True
    # created_at은 UTC(tz 없이 저장되는 경우도 있다)라 KST 날짜로 옮겨 비교한다.
    at = latest_at if latest_at.tzinfo else latest_at.replace(tzinfo=UTC)
    return at.astimezone(_RANK_RECOMPUTE_TZ).date() < now.date()


async def _ranking_shift_scheduler() -> None:
    import logging

    from app.db.session import AsyncSessionLocal
    from app.domain.feed.service import RankingShiftService

    log = logging.getLogger(__name__)
    last_try: date | None = None
    while True:
        try:
            async with AsyncSessionLocal() as session:
                service = RankingShiftService(session)
                if _rank_recompute_due(await service.latest_snapshot_at(), last_try):
                    await service.recompute_daily(await _rank_entries_computer(session))
                    # 성공한 뒤에 표시한다 — 먼저 표시하면 한 번 실패한 날이 통째로
                    # 건너뛰어진다. 이렇게 두면 잠깐의 실패는 다음 확인에서 회복되고,
                    # 계속 실패하면 10분마다 로그가 남아 눈에 띈다.
                    last_try = datetime.now(_RANK_RECOMPUTE_TZ).date()
                    log.info("랭크 스냅샷 재집계 완료")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            # 한 번 실패해도 다음 확인에서 다시 시도한다 — 루프를 죽이지 않는다.
            log.exception("랭크 스냅샷 재집계 실패")
        await asyncio.sleep(_RANK_CHECK_INTERVAL_SEC)


async def _migrate_feed_target_types(conn) -> None:
    """feed_comments.target_type에 저장된 옛 값을 지금 이름으로 옮긴다(멱등).

    댓글이 가리키는 대상 종류는 문자열 그대로 컬럼에 들어가므로, 이름 정리(요청)로
    match→gameResult·rankshift→rankingShift가 되면서 쌓여 있던 값도 함께 옮겨야 한다.
    옛 값이 하나도 없으면 아무 일도 안 한다. 받는 쪽(schemas.normalize_target_type)이
    옛 값도 계속 새 이름으로 바꿔 주므로, 배포가 어긋난 순간에 들어온 댓글도 안전하다.
    """
    import logging

    from sqlalchemy import text

    from app.domain.feed.schemas import LEGACY_FEED_TARGET_TYPES

    try:
        for old, new in LEGACY_FEED_TARGET_TYPES.items():
            res = await conn.execute(
                text("UPDATE feed_comments SET target_type = :new WHERE target_type = :old"),
                {"new": new, "old": old},
            )
            if res.rowcount:
                logging.getLogger(__name__).info(
                    "댓글 대상 종류 이름 변경: %s -> %s (%s건)", old, new, res.rowcount,
                )
    except Exception:  # noqa: BLE001 — 실패해도 부팅은 막지 않는다(옛 값 그대로 남는다).
        logging.getLogger(__name__).exception("댓글 대상 종류 이름 변경 실패")


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
        "SELECT id, 'gameResult', match_id, text, created_at, updated_at, created_by, updated_by FROM match_notes"
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
    """옛 요약 문장 컬럼(game_outcomes.summary TEXT)을 지운다.

    구조화된 summary_data로 갈아탄 뒤로는 코드 어디서도 읽지 않는다(6b7dc37) — 그때는
    "안 읽으면 그만"이라 물리 컬럼을 남겨 뒀지만, 이제 정리한다(요청). 옛 문장은 지금
    문구 규칙과 맞지 않아 되살릴 값이 아니고, 기존 경기는 리플레이를 다시 올려 채운다.

    컬럼이 없으면(새 DB) 아무것도 하지 않는다.
    """
    import logging

    from sqlalchemy import inspect, text

    def _has(sync_conn) -> bool:
        insp = inspect(sync_conn)
        if "game_outcomes" not in insp.get_table_names():
            return False
        return any(c["name"] == "summary" for c in insp.get_columns("game_outcomes"))

    try:
        if not await conn.run_sync(_has):
            return
        await conn.execute(text("ALTER TABLE game_outcomes DROP COLUMN summary"))
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
            text("SELECT COUNT(*) FROM feed_comments WHERE target_type = 'gameResult'")
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
    task = asyncio.create_task(_ranking_shift_scheduler())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


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
