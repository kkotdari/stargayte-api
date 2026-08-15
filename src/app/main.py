import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import settings
from app.domain.activity.models import ActivityComment, ActivityCommentMention
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
    from app.domain.activity import models as _activity_models  # noqa: F401
    from app.domain.game_results import models as _game_results_models  # noqa: F401
    from app.domain.members import models as _members_models  # noqa: F401
    from app.domain.schedules import models as _schedules_models  # noqa: F401

    # 각 단계를 사바포인트(begin_nested)로 감싼다. 이게 없으면 PostgreSQL에서는 단계 하나가
    # 실패한 순간 트랜잭션 전체가 aborted가 되고, 그 뒤 단계들은 무엇을 하든
    # InFailedSQLTransactionError로 줄줄이 죽는다 — 각 단계가 try/except로 실패를 삼켜도
    # 소용없다. 파이썬 쪽에서만 삼켰지 DB 쪽 트랜잭션은 이미 망가져 있기 때문이다. 실제로
    # 그렇게 부팅이 통째로 실패했다(운영 로그: "DROP TABLE IF EXISTS match_request_recommends"
    # 자리에서 InFailedSQLTransactionError — 정작 그 문장은 죄가 없고, 훨씬 앞선 단계가
    # 트랜잭션을 이미 망가뜨린 뒤였다). 사바포인트로 감싸면 실패한 단계만 되돌아가고
    # 나머지는 제 할 일을 한다.
    #
    # 실패는 삼키되 반드시 남긴다 — 조용히 넘어가면 "왜 이 컬럼이 없지"를 한참 뒤에
    # 딴 데서 만나게 된다.
    import logging

    from sqlalchemy import text

    log = logging.getLogger(__name__)

    async def step(name: str, run) -> None:
        try:
            async with conn.begin_nested():
                await run()
        except Exception:  # noqa: BLE001 — 한 단계가 실패해도 나머지와 부팅은 계속한다.
            log.exception("스키마 단계 실패: %s", name)

    async with engine.begin() as conn:
        # 랭킹변동(rank_shifts)은 스냅샷(rank_snapshots) 방식으로 대체됐다 — 옛 테이블은
        # 쌓인 데이터까지 함께 버린다(요청: 기존 랭킹변동 저장 테이블 드롭).
        await step("drop rank_shifts", lambda: conn.execute(text("DROP TABLE IF EXISTS rank_shifts")))
        # 이름 정리(요청: 계층에 맞춰 통일)로 테이블 몇 개의 이름이 바뀌었다 — create_all보다
        # 반드시 먼저 돌려야 한다. 나중에 돌리면 create_all이 새 이름의 빈 테이블을 먼저
        # 만들어 버려서, 옛 테이블은 데이터를 안은 채 이름을 못 바꾸고 남는다.
        await step("rename legacy tables", lambda: _rename_legacy_tables(conn))
        # 테이블 이름을 바꿔도 그 테이블이 쓰는 시퀀스는 옛 이름 그대로 남는다 — 이름을
        # 맞춰 준다(요청). 표 이름을 바꾼 뒤에 돌려야 하므로 바로 여기다.
        await step("rename owned sequences", lambda: _rename_owned_sequences(conn))
        # 테이블을 하나씩 만든다 — create_all 한 번으로 몰면 한 테이블의 DDL이 실패할 때
        # 그 사바포인트가 통째로 되돌아가, 죄 없는 나머지 테이블까지 하나도 안 생긴다.
        #
        # 한 테이블이 세워도 나머지는 만들어져야 한다. 이번 사고에서 부딪힌 문제는 아니지만
        # (그건 이름이 갈라진 것이었다) 사바포인트를 단계마다 두는 이 파일의 취지가
        # create_all 한 덩어리에는 안 걸려 있었다 — 여기만 예외로 둘 이유가 없다.
        #
        # sorted_tables는 외래키 의존 순서라, 앞 것이 실패해도 뒤 것은 제 부모가 이미 있는
        # 한 정상적으로 만들어진다. checkfirst=True(기본)라 이미 있는 테이블은 건너뛴다.
        for table in Base.metadata.sorted_tables:
            await step(
                f"create table {table.name}",
                lambda t=table: conn.run_sync(t.create, checkfirst=True),
            )
        for name, fn in (
            ("migrate match notes", _migrate_match_notes),
            ("migrate activity target types", _migrate_activity_target_types),
            ("add game_outcomes.summary_data", _add_match_result_summary),
            ("add game_outcomes.map_hash", _add_match_result_map_hash),
            ("add replay map resources", _add_replay_map_resources),
            ("add replay map image id", _add_replay_map_image_id),
            ("add replay map linked by", _add_replay_map_linked_by),
            ("add game result view count", _add_game_result_view_count),
            ("add league match schedule_posted_at", _add_league_match_schedule_posted_at),
            ("add challenge time note", _add_challenge_time_note),
            ("add challenge canceled_by", _add_challenge_canceled_by),
            ("add challenge backdrop", _add_challenge_backdrop),
            ("add participant build_mix", _add_participant_build_mix),
            ("add minimap image walk", _add_minimap_image_walk),
            ("drop challenge time", _drop_challenge_time),
            ("drop challenge revenge chain", _drop_challenge_revenge_chain),
            ("drop challenge from match request", _drop_challenge_from_match_request),
            ("drop match request tables", _drop_match_request_tables),
            ("drop access screen code check", _drop_access_screen_code_check),
            ("add access history detail", _add_access_history_detail),
            ("drop member epithets", _drop_member_epithets),
            ("drop legacy match notes", _drop_legacy_match_notes),
            ("drop legacy match summary", _drop_legacy_match_summary),
            ("rebuild ranking shifts", _rebuild_ranking_shifts),
        ):
            await step(name, (lambda f=fn: f(conn)))
    await _seed_ranking_shifts()


# 생 SQL이 쓰는 활동 댓글 테이블 이름 — 손으로 적지 않고 모델에서 가져온다.
#
# 손으로 적었다가 실제로 운영을 세웠다: 이름 일괄 변경(피드 → 활동) 때 이 파일의 SQL
# 문자열까지 함께 치환됐는데 정작 __tablename__은 그대로여서, 부팅 도중 "UPDATE
# activity_comments …"가 없는 테이블을 건드렸다. PostgreSQL은 그 한 번으로 트랜잭션을
# aborted로 만들고, 뒤따르는 모든 단계가 InFailedSQLTransactionError로 죽었다.
# 모델에서 가져오면 이름이 어디로 바뀌든 SQL과 스키마가 갈라설 수가 없다.
_COMMENTS = ActivityComment.__tablename__
_MENTIONS = ActivityCommentMention.__tablename__

# 이름 정리(요청) 이전에 쓰던 테이블 이름 → 지금 이름. 활동 1뎁스가 게임결과/너 나와/
# 랭크변동이고 게임결과 카드 안의 2뎁스가 게임결과 카드라는 계층에 맞춘 것이다.
_TABLE_RENAMES = [
    ("matches", "game_results"),
    ("match_participants", "game_result_participants"),
    ("match_results", "game_outcomes"),
    ("rank_snapshots", "ranking_shifts"),
    # 활동 댓글 두 테이블 — 코드에서 feed/post 표현을 다 걷어내면서 이름을 맞춘다(요청).
    # 한 번 되돌린 적이 있는데, 그 되돌림이 오히려 코드(feed_comments)와 운영
    # DB(activity_comments)를 갈라놓아 활동 목록이 통째로 500이었다. 운영은 이미 새 이름을
    # 쓰고 있으므로 여기서는 아무 일도 안 일어나고(멱등), 옛 이름으로 남은 DB만 옮겨진다.
    ("feed_comments", "activity_comments"),
    ("feed_comment_mentions", "activity_comment_mentions"),
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


async def _rename_owned_sequences(conn) -> None:
    """시퀀스 이름을 제 테이블에 맞춘다(멱등, PostgreSQL 전용).

    ALTER TABLE … RENAME TO는 그 테이블이 쓰는 시퀀스까지 따라 바꾸지는 않는다. 그래서
    이름을 옮긴 테이블마다 옛 이름의 시퀀스가 남는다 — 운영에서 실제로 feed_comments_id_seq,
    matches_id_seq, rank_snapshots_id_seq처럼 지금은 없는 이름이 줄줄이 남아 있었다(지적).
    동작에는 지장이 없지만(테이블은 oid로 제 시퀀스를 붙들고 있다) DB를 열어 보는 사람에게는
    없는 테이블의 흔적이라 읽기 나쁘고, 덤프를 옮겨 심을 때 헷갈린다.

    이름을 손으로 적지 않고 카탈로그에서 '그 시퀀스를 소유한 테이블·컬럼'을 직접 물어
    표준 이름({테이블}_{컬럼}_seq)과 다른 것만 바꾼다 — 그래서 오늘 옮긴 것뿐 아니라
    예전에 어긋난 것까지 함께 정리되고, 앞으로 이름을 또 옮겨도 이 함수는 그대로 맞는다.

    SQLite에는 시퀀스가 없어 아무 일도 안 한다.
    """
    import logging

    from sqlalchemy import text

    log = logging.getLogger(__name__)
    if conn.dialect.name != "postgresql":
        return

    rows = (await conn.execute(text("""
        SELECT s.relname AS seq, t.relname AS tbl, a.attname AS col
        FROM pg_class s
        JOIN pg_depend d
          ON d.objid = s.oid
         AND d.classid = 'pg_class'::regclass
         AND d.refclassid = 'pg_class'::regclass
        JOIN pg_class t ON t.oid = d.refobjid
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
        JOIN pg_namespace n ON n.oid = s.relnamespace
        WHERE s.relkind = 'S' AND n.nspname = current_schema()
    """))).all()

    for seq, tbl, col in rows:
        want = f"{tbl}_{col}_seq"
        if seq == want:
            continue
        try:
            # 바꾸려는 이름이 이미 있으면 건드리지 않는다 — 남의 시퀀스를 덮어쓰느니 옛
            # 이름으로 남는 편이 낫다.
            await conn.execute(text(f'ALTER SEQUENCE "{seq}" RENAME TO "{want}"'))
            log.info("시퀀스 이름 변경: %s -> %s", seq, want)
        except Exception:  # noqa: BLE001 — 실패해도 부팅은 막지 않는다(옛 이름 그대로 남는다).
            log.exception("시퀀스 이름 변경 실패: %s -> %s", seq, want)


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


async def _add_replay_map_resources(conn: object) -> None:
    """replay_maps.resources 컬럼을 더한다(멱등) — 자원 지대 좌표. 옛 맵 행은 NULL로 남고,
    같은 리플레이를 다시 올려 자원이 함께 저장되면 새 해시로 새 행이 생긴다."""
    import logging

    from sqlalchemy import text

    try:
        await conn.execute(  # type: ignore[attr-defined]
            text("ALTER TABLE replay_maps ADD COLUMN IF NOT EXISTS resources JSONB")
        )
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).debug("replay_maps.resources 컬럼 추가 건너뜀", exc_info=True)


async def _add_replay_map_image_id(conn: object) -> None:
    """replay_maps.image_id 컬럼을 더한다(멱등) — 사람이 올려 둔 실제 미니맵 그림
    (minimap_images)을 가리킨다. 여러 맵 행이 같은 그림을 가리킬 수 있다(요청: 이름·판본만
    다른 거의 같은 맵을 한데 묶기). 새 테이블 자체는 create_all이 만든다.

    FK는 여기서 걸지 않는다 — 이미 있는 테이블에 FK를 더하려면 PostgreSQL은 되지만 SQLite는
    테이블을 다시 만들어야 한다. 모델에 선언된 FK는 새로 만드는 DB(create_all)에만 붙고,
    운영 중인 DB에서는 컬럼만 더해도 동작에 차이가 없다(정리는 서비스 쪽에서 한다)."""
    import logging

    from sqlalchemy import text

    # IF NOT EXISTS는 PostgreSQL(운영)만 알아듣는다 — SQLite(개발/테스트)에서는 구문 오류가
    # 나므로 그냥 ADD COLUMN으로 한 번 더 시도하고, 이미 있으면 그때 나는 오류를 삼킨다.
    for sql in (
        "ALTER TABLE replay_maps ADD COLUMN IF NOT EXISTS image_id BIGINT",
        "ALTER TABLE replay_maps ADD COLUMN image_id BIGINT",
    ):
        try:
            await conn.execute(text(sql))  # type: ignore[attr-defined]
            return
        except Exception:  # noqa: BLE001
            continue
    logging.getLogger(__name__).debug("replay_maps.image_id 컬럼 추가 건너뜀", exc_info=True)


async def _add_game_result_view_count(conn: object) -> None:
    """game_results.view_count 컬럼을 더한다(멱등) — 게임 상세 페이지 조회수(요청)."""
    import logging

    from sqlalchemy import text

    for sql in (
        "ALTER TABLE game_results ADD COLUMN IF NOT EXISTS view_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE game_results ADD COLUMN view_count INTEGER NOT NULL DEFAULT 0",
    ):
        try:
            await conn.execute(text(sql))  # type: ignore[attr-defined]
            return
        except Exception:  # noqa: BLE001
            continue
    logging.getLogger(__name__).debug("game_results.view_count 컬럼 추가 건너뜀", exc_info=True)


async def _add_replay_map_linked_by(conn: object) -> None:
    """replay_maps.linked_by / linked_at 컬럼을 더한다(멱등) — 게임 상세의 맵연결(요청:
    아무나 미니맵 그림을 골라 연결)에서 누가 언제 마지막으로 연결했는지 남기는 자리다.

    FK는 여기서 걸지 않는다 — _add_replay_map_image_id와 같은 이유(SQLite는 기존 테이블에
    FK를 못 더한다). 모델의 FK는 새로 만드는 DB에만 붙는다."""
    import logging

    from sqlalchemy import text

    # IF NOT EXISTS는 PostgreSQL(운영)만 알아듣는다 — SQLite(개발/테스트)에서는 구문 오류가
    # 나므로 그냥 ADD COLUMN으로 한 번 더 시도하고, 이미 있으면 그때 나는 오류를 삼킨다.
    for col, sqls in (
        ("linked_by", (
            "ALTER TABLE replay_maps ADD COLUMN IF NOT EXISTS linked_by BIGINT",
            "ALTER TABLE replay_maps ADD COLUMN linked_by BIGINT",
        )),
        ("linked_at", (
            "ALTER TABLE replay_maps ADD COLUMN IF NOT EXISTS linked_at TIMESTAMPTZ",
            "ALTER TABLE replay_maps ADD COLUMN linked_at TIMESTAMP",
        )),
    ):
        for sql in sqls:
            try:
                await conn.execute(text(sql))  # type: ignore[attr-defined]
                break
            except Exception:  # noqa: BLE001
                continue
        else:
            logging.getLogger(__name__).debug("replay_maps.%s 컬럼 추가 건너뜀", col, exc_info=True)


async def _add_league_match_schedule_posted_at(conn: object) -> None:
    """league_matches.schedule_posted_at 컬럼을 더한다(멱등).

    일정을 처음 적어 둔 때 — 활동 목록에서 이 경기가 언제 새것이었는지의 기준이다
    (models.py 주석 참고). 이미 일정이 적혀 있는 옛 줄은 NULL로 남는데, 그러면 활동에
    안 뜬다: 지난 일정을 이제 와서 전부 새것으로 올릴 이유가 없다. 다음에 그 일정을
    손대면 그때부터 뜬다.
    """
    import logging

    from sqlalchemy import text

    # IF NOT EXISTS는 PostgreSQL(운영)만 알아듣는다 — SQLite(개발/테스트)에서는 구문 오류가
    # 나므로 그냥 ADD COLUMN으로 한 번 더 시도하고, 이미 있으면 그때 나는 오류를 삼킨다.
    for sql in (
        "ALTER TABLE league_matches ADD COLUMN IF NOT EXISTS schedule_posted_at TIMESTAMPTZ",
        "ALTER TABLE league_matches ADD COLUMN schedule_posted_at TIMESTAMP",
    ):
        try:
            await conn.execute(text(sql))  # type: ignore[attr-defined]
            return
        except Exception:  # noqa: BLE001
            continue
    logging.getLogger(__name__).debug("league_matches.schedule_posted_at 컬럼 추가 건너뜀", exc_info=True)


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


async def _add_challenge_canceled_by(conn: object) -> None:
    """challenges.canceled_by_pk 컬럼을 더한다(멱등).

    위 _add_challenge_time_note와 같은 이유 — create_all은 이미 있는 테이블에 새 컬럼을
    넣어주지 않는다. 폐기가 '취소'였는지(누가 거둬들였는지)를 담는 자리다(요청: 활동에
    거절/무응답거절/취소를 갈라 보여주기). NULL이면 취소가 아닌 폐기다.
    """
    import logging

    from sqlalchemy import text

    # SQLite는 ADD COLUMN IF NOT EXISTS를 모른다(개발용 DB가 그렇다) — 그 문법이 막히면
    # 조건 없는 ADD COLUMN으로 한 번 더 시도한다. 이미 있으면 그쪽이 에러를 내고 넘어간다.
    for sql in (
        "ALTER TABLE challenges ADD COLUMN IF NOT EXISTS canceled_by_pk BIGINT",
        "ALTER TABLE challenges ADD COLUMN canceled_by_pk BIGINT",
    ):
        try:
            await conn.execute(text(sql))  # type: ignore[attr-defined]
            return
        except Exception:  # noqa: BLE001 — 다음 문법으로 넘어가거나, 이미 있으면 그대로 둔다.
            logging.getLogger(__name__).debug("challenges.canceled_by_pk 추가 시도 실패: %s", sql, exc_info=True)


async def _add_challenge_backdrop(conn: object) -> None:
    """challenges의 편지지 배경 사진 컬럼 네 개를 더한다(멱등).

    위 _add_challenge_canceled_by와 같은 이유 — create_all은 이미 있는 테이블에 새 컬럼을
    넣어주지 않는다. 부르는 사람이 호출할 때 올린 편지지 배경 사진 자리다(요청). 그림이
    두 장인 이유는 models.py 주석 참고 — 카카오 카드판에는 로고와 문구가 구워져 있어
    편지지 배경으로는 쓸 수 없다. 옛 호출은 NULL로 남아 예전 그대로 보인다.
    """
    import logging

    from sqlalchemy import text

    for column, kind in (
        ("backdrop_url", "TEXT"),
        ("backdrop_share_url", "TEXT"),
        # 공유 카드판의 실제 크기 — 카카오에 함께 넘겨야 사진의 원래 비율로 앉는다(요청:
        # "카톡 미리보기는 원본 비율"). 처음엔 2:1로 잘라 만들었기에 크기가 늘 같아서
        # 적어 둘 이유가 없었다.
        ("backdrop_share_width", "INTEGER"),
        ("backdrop_share_height", "INTEGER"),
    ):
        # SQLite는 ADD COLUMN IF NOT EXISTS를 모른다(개발용 DB가 그렇다) — 방언별 두 형태를
        # 차례로 시도한다. 이미 있으면 둘 다 실패하고 그게 정상이다.
        #
        # 시도마다 사바포인트를 따로 여는 것이 핵심이다: 포스트그레스는 문장 하나가 실패하면
        # 그 트랜잭션 전체를 중단 상태로 만들어, 그 뒤 문장은 무엇이든 다시 터진다. 한 단계
        # 안에서 두 컬럼을 잇달아 손대는 것은 이 파일에서 여기뿐이라, 첫 컬럼이 이미 있다는
        # 이유만으로 둘째 컬럼이 영영 안 생기는 일이 실제로 가능하다.
        for sql in (
            f"ALTER TABLE challenges ADD COLUMN IF NOT EXISTS {column} {kind}",
            f"ALTER TABLE challenges ADD COLUMN {column} {kind}",
        ):
            try:
                async with conn.begin_nested():  # type: ignore[attr-defined]
                    await conn.execute(text(sql))  # type: ignore[attr-defined]
                break
            except Exception:  # noqa: BLE001 — 다음 형태로 넘어가거나, 이미 있으면 그대로 둔다.
                logging.getLogger(__name__).debug("challenges.%s 추가 시도 실패", column, exc_info=True)


async def _add_participant_build_mix(conn: object) -> None:
    """game_result_participants.build_mix 컬럼을 더한다(멱등).

    그 경기에서 무엇을 짓고 무엇을 뽑았나의 구성이다(요청: 통계 생산 칸에 도넛 셋 + 초반
    일꾼 수). 옛 경기는 NULL로 남고, 그런 경기만 있는 회원은 화면에서 도넛 없이 총량만
    보인다 — 리플레이를 다시 올리면(머지) 그때 채워진다.
    """
    import logging

    from sqlalchemy import text

    # SQLite는 ADD COLUMN IF NOT EXISTS를 모른다(위 _add_challenge_canceled_by 참고).
    # 타입은 JSON으로 둔다 — Postgres는 그대로, SQLite는 TEXT로 받아 준다.
    for sql in (
        "ALTER TABLE game_result_participants ADD COLUMN IF NOT EXISTS build_mix JSON",
        "ALTER TABLE game_result_participants ADD COLUMN build_mix JSON",
    ):
        try:
            await conn.execute(text(sql))  # type: ignore[attr-defined]
            return
        except Exception:  # noqa: BLE001 — 다음 문법으로 넘어가거나, 이미 있으면 그대로 둔다.
            logging.getLogger(__name__).debug("build_mix 추가 시도 실패: %s", sql, exc_info=True)


async def _add_minimap_image_walk(conn: object) -> None:
    """minimap_images.walk 컬럼을 더한다(멱등) - 지형(이동 가능/불가) 격자(요청:
    운영자가 검수/수정한 값 저장). 방언 갈래는 build_mix와 같다."""
    from sqlalchemy import text

    for sql in (
        "ALTER TABLE minimap_images ADD COLUMN IF NOT EXISTS walk TEXT",
        "ALTER TABLE minimap_images ADD COLUMN walk TEXT",
    ):
        try:
            await conn.execute(text(sql))  # type: ignore[attr-defined]
            return
        except Exception:  # noqa: BLE001
            continue



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


async def _drop_challenge_from_match_request(conn) -> None:
    """challenges.from_match_request("요청대결" 배지 플래그)를 지운다(멱등).

    "너 나와! 요청"(match_requests) 기능이 통째로 없어져(요청) 그 요청을 "들어주기"로 받아
    만들어진 도전장이라는 표식도 뜻이 없어졌다 — 배지를 그리던 화면도 함께 사라졌다.

    아래 _drop_match_request_tables와 짝이다. IF EXISTS는 PostgreSQL만 받으므로(SQLite는
    DROP COLUMN까지만) 있는지 먼저 보고 조건 없는 DROP COLUMN을 날린다.
    """
    import logging

    from sqlalchemy import inspect, text

    try:
        cols = await conn.run_sync(
            lambda c: {col["name"] for col in inspect(c).get_columns("challenges")}
        )
        if "from_match_request" not in cols:
            return
        await conn.execute(text("ALTER TABLE challenges DROP COLUMN from_match_request"))
        logging.getLogger(__name__).info("challenges.from_match_request 삭제 완료")
    except Exception:  # noqa: BLE001 — 실패해도 부팅은 막지 않는다(안 읽는 컬럼으로 남는다).
        logging.getLogger(__name__).warning(
            "challenges.from_match_request 삭제 건너뜀", exc_info=True,
        )


async def _drop_match_request_tables(conn) -> None:
    """"너 나와! 요청"(match_requests) 세 테이블을 통째로 지운다(멱등).

    "이런 대결 봤으면 좋겠다"를 남기고 다른 회원이 추천(엄지척)하던 공개 요청글 코너다.
    등록/목록/추천/완료 화면은 이미 없어졌고 인박스(언급 알림)만 남아 있었는데, 프론트에서
    그것마저 걷어내(요청) 이 테이블들을 읽는 코드가 하나도 남지 않았다 — API·모델과 함께
    저장소도 정리한다. 되살릴 값이 아니다(경기 기록·포인트와는 무관한 게시판이었다).

    자식(match_request_targets/recommends)을 먼저 지워야 외래키가 걸리지 않는다.
    """
    import logging

    from sqlalchemy import text

    try:
        for table in (
            "match_request_recommends",
            "match_request_targets",
            "match_requests",
        ):
            await conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
        logging.getLogger(__name__).info("너 나와! 요청(match_requests) 테이블 삭제 완료")
    except Exception:  # noqa: BLE001 — 실패해도 부팅은 막지 않는다(안 쓰는 테이블로 남는다).
        logging.getLogger(__name__).exception("너 나와! 요청(match_requests) 테이블 삭제 실패")


async def _add_access_history_detail(conn: object) -> None:
    """access_history.detail 컬럼을 더한다(멱등).

    create_all은 이미 있는 테이블에 새 컬럼을 넣어주지 않는다(위 _add_challenge_time_note와
    같은 이유). 공유 링크로 열린 카드가 무엇이었는지를 적는 자리다(요청: "접속로그에
    공유페이지 열어본거도 표시(어떤 페이지인지도)") — 화면 코드는 다 같은 "share"라
    이 칸이 없으면 무엇을 열어 봤는지가 통째로 안 남는다.
    """
    import logging

    from sqlalchemy import text

    # SQLite는 ADD COLUMN IF NOT EXISTS를 모른다 — 운영(Postgres)만 보고 그 한 줄로 두면
    # 로컬/테스트 DB에는 컬럼이 영영 안 생기고, 그 사실이 부팅 로그에 debug로만 남아
    # 조용히 지나간 뒤 첫 로그인에서 "no column named detail"로 터진다(실제로 겪었다).
    # 그래서 방언별 두 형태를 차례로 시도한다 — 이미 있으면 둘 다 실패하고 그게 정상이다.
    for sql in (
        "ALTER TABLE access_history ADD COLUMN IF NOT EXISTS detail VARCHAR(64)",
        "ALTER TABLE access_history ADD COLUMN detail VARCHAR(64)",
    ):
        try:
            await conn.execute(text(sql))  # type: ignore[attr-defined]
            return
        except Exception:  # noqa: BLE001 — 다음 형태로 넘어간다.
            continue
    logging.getLogger(__name__).debug("access_history.detail 컬럼 추가 건너뜀(이미 있음)")


async def _drop_access_screen_code_check(conn: object) -> None:
    """access_history의 낡은 화면코드 CHECK 제약을 떼어낸다(멱등).

    이 제약은 테이블이 처음 만들어질 때의 화면 목록으로 굳어 있는데, 스키마를 create_all로만
    관리해(마이그레이션 없음) 코드에서 목록을 고쳐도 기존 DB에는 영원히 반영되지 않았다 —
    그래서 새 화면(activity 등)의 접속 기록이 INSERT 단계에서 조용히 터졌다. 검증은 API 계층
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
    """랭크 스냅샷 계산기 — 활동 도메인이 경기 통계를 되부르는 순환을 피해 콜백으로 넘긴다."""
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

    변동분 없이(reason="seed") 저장되므로 활동에는 안 보인다. 실패해도 부팅은 막지 않는다.
    """
    import logging

    from app.db.session import AsyncSessionLocal
    from app.domain.activity.service import RankingShiftService

    # 기능이 꺼져 있으면 기준선도 안 깐다(요청) — 손으로 비운 표가 다음 부팅에 다시 찬다.
    if not settings.ranking_shift_enabled:
        return
    try:
        async with AsyncSessionLocal() as session:
            await RankingShiftService(session).seed_if_empty(await _rank_entries_computer(session))
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("랭크 스냅샷 기준선 적재 실패")


# 정해진 시각마다 순위표를 다시 집계한다(요청) — 예전처럼 경기 등록/삭제마다 계산하면
# 하루에도 여러 번 변동 카드가 떠서 활동이 그 카드로 도배됐다. 한동안 모아 한 번만 남긴다.
# 지금은 자정과 정오, 하루 두 번이다(요청) — 한 번(아침)일 때는 밤에 몰아친 경기가 다음 날
# 아침까지 순위표에 안 잡혔다. 두 번이라고 카드가 두 배가 되진 않는다: 집계는 직전 스냅샷과
# 견줘 달라진 게 없으면 아무 행도 안 남기므로, 조용한 반나절은 그냥 지나간다.
#
# 예전에는 "다음 자정까지 남은 초만큼 sleep" 하나로 만들어 뒀는데, 그러면 그 순간에 프로세스가
# 살아 있어야만 돈다 — 그리고 실제로 안 돌았다(지적). 이 앱은 새벽에 아무도 안 쓰니 그때
# 컨테이너가 잠들거나(무료/저트래픽 플랜) 배포·재시작으로 프로세스가 새로 뜨는 일이 잦고,
# 새로 뜨면 또 '다음 자정'을 기다리기 시작하므로 그 하루는 통째로 건너뛴다. 놓친 것을
# 알아채는 장치도 없었다.
#
# 그래서 '정확한 시각에 깨어나기'를 버리고 '밀린 일을 찾아 하기'로 바꿨다: 짧은 주기로 깨어나
# ① 지금이 어느 구간인지 보고 ② 그 구간 것을 아직 안 남겼으면 그때 집계한다. 부팅 직후에도
# 같은 검사를 하므로, 목표 시각에 잠들어 있었더라도 그 뒤 처음 누가 앱을 열면 그때 돈다.
# 이 방식은 상태를 DB에서 읽으므로 재시작에 영향받지 않는다 — 자정처럼 아무도 안 쓰는
# 시각을 목표로 삼아도 되는 이유가 이것이다.
_RANK_RECOMPUTE_TZ = ZoneInfo("Asia/Seoul")
# 밀린 일이 있는지 확인하는 주기. 짧게 두는 건 잠에서 깬 직후를 빨리 잡기 위해서고, 확인
# 자체는 스냅샷 한 줄을 읽는 것뿐이라 부담이 없다.
_RANK_CHECK_INTERVAL_SEC = 10 * 60


def _rank_slot_start(now: datetime) -> datetime:
    """지금이 속한 집계 구간의 시작 시각 — now까지(포함) 지나온 가장 최근 목표 시각.

    하루 한 번이던 시절엔 '날짜'가 곧 구간이었지만, 하루 여러 번이 되면 날짜만으로는
    오전 것과 오후 것을 못 가른다. 구간을 시각으로 잡아 두면 "이 구간 것은 이미 남겼나"가
    스냅샷 시각 한 번의 비교로 끝난다.

    오늘 첫 목표 시각도 아직 안 지났으면 어제의 마지막 구간에 있는 것이다.
    """
    hours = settings.rank_recompute_hours  # 설정에서 정렬·중복제거를 마친 값
    today = [now.replace(hour=h, minute=0, second=0, microsecond=0) for h in hours]
    passed = [t for t in today if t <= now]
    return passed[-1] if passed else today[-1] - timedelta(days=1)


def _rank_recompute_due(latest_at: datetime | None, slot: datetime) -> bool:
    """이 구간 것을 아직 안 남겼나 — 마지막 스냅샷이 구간 시작보다 앞서면 남길 차례다.

    구간 안에 이미 스냅샷이 있으면 건너뛰는 게 핵심이다. 정오에 한 번 돌고 오후에 경기가
    등록된 뒤 재시작이 걸려도, 그 검사가 있어야 같은 구간에 두 번째 변동 카드가 안 뜬다.
    """
    if latest_at is None:
        return True
    # created_at은 UTC(tz 없이 저장되는 경우도 있다)라 KST로 옮겨 비교한다.
    at = latest_at if latest_at.tzinfo else latest_at.replace(tzinfo=UTC)
    return at.astimezone(_RANK_RECOMPUTE_TZ) < slot


async def _ranking_shift_scheduler() -> None:
    import logging

    from app.db.session import AsyncSessionLocal
    from app.domain.activity.service import RankingShiftService

    log = logging.getLogger(__name__)
    # 꺼져 있으면 루프를 아예 안 돈다(요청) — 여기서 막아 두면 부르는 쪽(lifespan)이 켜짐
    # 여부를 몰라도 되고, 스케줄러를 직접 부르는 테스트도 그대로 이 규칙을 따른다.
    if not settings.ranking_shift_enabled:
        log.info("랭크 변동 집계가 꺼져 있어 스케줄러를 띄우지 않는다")
        return
    # 이 프로세스에서 마지막으로 집계를 마친 구간. DB만 보면 안 되는 이유: 순위표가 그대로인
    # 구간은 recompute_daily가 아무 행도 남기지 않으므로 '아직 안 했다'로 계속 읽혀 10분마다
    # 헛돌게 된다.
    last_slot: datetime | None = None
    while True:
        try:
            slot = _rank_slot_start(datetime.now(_RANK_RECOMPUTE_TZ))
            if slot != last_slot:
                async with AsyncSessionLocal() as session:
                    service = RankingShiftService(session)
                    if _rank_recompute_due(await service.latest_snapshot_at(), slot):
                        await service.recompute_daily(await _rank_entries_computer(session))
                        log.info("랭크 스냅샷 재집계 완료 — %s 구간", slot.isoformat())
                    # 성공한 뒤에 표시한다 — 먼저 표시하면 한 번 실패한 구간이 통째로
                    # 건너뛰어진다. 이렇게 두면 잠깐의 실패는 다음 확인에서 회복되고,
                    # 계속 실패하면 10분마다 로그가 남아 눈에 띈다.
                    last_slot = slot
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            # 한 번 실패해도 다음 확인에서 다시 시도한다 — 루프를 죽이지 않는다.
            log.exception("랭크 스냅샷 재집계 실패")
        await asyncio.sleep(_RANK_CHECK_INTERVAL_SEC)


async def _migrate_activity_target_types(conn) -> None:
    """활동 댓글 테이블의 target_type에 저장된 옛 값을 지금 이름으로 옮긴다(멱등).

    댓글이 가리키는 대상 종류는 문자열 그대로 컬럼에 들어가므로, 이름 정리(요청)로
    match→gameResult·rankshift→rankingShift가 되면서 쌓여 있던 값도 함께 옮겨야 한다.
    옛 값이 하나도 없으면 아무 일도 안 한다. 받는 쪽(schemas.normalize_target_type)이
    옛 값도 계속 새 이름으로 바꿔 주므로, 배포가 어긋난 순간에 들어온 댓글도 안전하다.
    """
    import logging

    from sqlalchemy import text

    from app.domain.activity.schemas import LEGACY_FEED_TARGET_TYPES

    try:
        for old, new in LEGACY_FEED_TARGET_TYPES.items():
            res = await conn.execute(
                text(f"UPDATE {_COMMENTS} SET target_type = :new WHERE target_type = :old"),
                {"new": new, "old": old},
            )
            if res.rowcount:
                logging.getLogger(__name__).info(
                    "댓글 대상 종류 이름 변경: %s -> %s (%s건)", old, new, res.rowcount,
                )
    except Exception:  # noqa: BLE001 — 실패해도 부팅은 막지 않는다(옛 값 그대로 남는다).
        logging.getLogger(__name__).exception("댓글 대상 종류 이름 변경 실패")


async def _migrate_match_notes(conn) -> None:
    """기존 경기 댓글(match_notes)을 일반화된 활동 댓글 테이블로 1회 이관.

    활동 댓글 테이블이 비어 있고 match_notes에 데이터가 있을 때만 복사한다(멱등).
    id를 그대로 보존해 언급(mentions) 매핑도 함께 옮긴다.

    이제 match_notes는 모델에서 빠져 create_all이 만들지 않는다 — 새로 만든 DB에는 아예
    없으므로, 테이블이 없으면 조용히 건너뛴다(이관할 것도 없다).
    """
    from sqlalchemy import inspect, text

    tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    if "match_notes" not in tables:
        return
    existing = await conn.scalar(text(f"SELECT COUNT(*) FROM {_COMMENTS}"))
    if existing and existing > 0:
        return
    legacy = await conn.scalar(text("SELECT COUNT(*) FROM match_notes"))
    if not legacy:
        return
    await conn.execute(text(
        f"INSERT INTO {_COMMENTS} (id, target_type, target_id, text, created_at, updated_at, created_by, updated_by) "
        "SELECT id, 'gameResult', match_id, text, created_at, updated_at, created_by, updated_by FROM match_notes"
    ))
    await conn.execute(text(
        f"INSERT INTO {_MENTIONS} (comment_id, member_pk) "
        "SELECT note_id, member_pk FROM match_note_mentions"
    ))
    if conn.dialect.name == "postgresql":
        await conn.execute(text(
            f"SELECT setval(pg_get_serial_sequence('{_COMMENTS}', 'id'), (SELECT MAX(id) FROM {_COMMENTS}))"
        ))
        await conn.execute(text(
            f"SELECT setval(pg_get_serial_sequence('{_MENTIONS}', 'id'), "
            f"(SELECT COALESCE(MAX(id), 1) FROM {_MENTIONS}))"
        ))



async def _rebuild_ranking_shifts(conn) -> None:
    """랭크 변동 스냅샷을 '하루 한 행 + 유형별 칸(sections)' 구조로 갈아엎는다.

    예전에는 유형(개인전/팀전)마다 한 행이었다. 카드를 한 장으로 합치면서(요청) 저장도
    하루 한 행으로 모았다 — 앞으로 유형이 늘어도 sections에 칸을 더하면 되므로 스키마를
    다시 안 건드린다.

    옛 행은 옮기지 않고 통째로 지운다(요청: "기존 로우 모두 삭제해줘도 돼, 트렁케이트 앤드
    리스타트 아이덴티티"). 스냅샷은 그날 순위표의 사진일 뿐이라, 다음 집계가 지금 성적으로
    기준선을 새로 깔면 그대로 복구된다.

    함께 지우는 것 하나 — 그 카드들에 달렸던 댓글(활동 댓글 테이블의 target_type='rankingShift').
    지우지 않으면 id가 1부터 다시 시작하면서 옛 댓글이 엉뚱한 날짜의 새 카드에 가서 붙는다.

    멱등: sections 컬럼이 이미 있으면(=이미 갈아엎은 DB) 아무 일도 안 한다.
    """
    import logging

    from sqlalchemy import inspect, text

    log = logging.getLogger(__name__)

    def _state(sync_conn) -> tuple[bool, set[str]]:
        insp = inspect(sync_conn)
        if "ranking_shifts" not in insp.get_table_names():
            return False, set()
        return True, {c["name"] for c in insp.get_columns("ranking_shifts")}

    try:
        exists, cols = await conn.run_sync(_state)
        if not exists or "sections" in cols:
            return
        # ① 옛 카드에 달린 댓글부터 — 아래에서 id가 1로 되감기므로 남겨 두면 오배달된다.
        await conn.execute(
            text(f"DELETE FROM {_COMMENTS} WHERE target_type = 'rankingShift'")
        )
        # ② 행을 비우고 id를 1로 되돌린다. TRUNCATE … RESTART IDENTITY는 PostgreSQL 전용이라
        #    실패하면 DELETE + SQLite 시퀀스 초기화로 물러선다.
        try:
            await conn.execute(text("TRUNCATE TABLE ranking_shifts RESTART IDENTITY"))
        except Exception:  # noqa: BLE001 — SQLite 등 TRUNCATE 미지원.
            await conn.execute(text("DELETE FROM ranking_shifts"))
            try:
                await conn.execute(
                    text("DELETE FROM sqlite_sequence WHERE name = 'ranking_shifts'")
                )
            except Exception:  # noqa: BLE001 — sqlite_sequence가 없는 DB면 그냥 넘어간다.
                pass
        # ③ 컬럼 갈아 끼우기. sections는 create_all이 안 만들어 주므로(이미 있는 테이블)
        #    여기서 더하고, 유형별 행 시절의 컬럼은 지운다.
        await conn.execute(
            text("ALTER TABLE ranking_shifts ADD COLUMN IF NOT EXISTS sections JSONB")
        )
        for col in ("match_type", "standings", "shifts"):
            try:
                await conn.execute(
                    text(f"ALTER TABLE ranking_shifts DROP COLUMN IF EXISTS {col}")
                )
            except Exception:  # noqa: BLE001 — 미지원 DB면 안 쓰는 컬럼으로 남겨 둔다.
                log.debug("ranking_shifts.%s 컬럼 삭제 건너뜀", col, exc_info=True)
        log.info("랭크 변동 스냅샷을 하루 한 행 구조로 재구성했습니다(옛 행·댓글 삭제)")
    except Exception:  # noqa: BLE001 — 실패해도 부팅은 막지 않는다.
        log.exception("랭크 변동 스냅샷 재구성 실패")


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

    바로 위 _migrate_match_notes가 같은 트랜잭션에서 먼저 돌아 남은 행을 활동 댓글 테이블로
    옮긴 뒤라, 여기서 지우는 건 이미 복사된 것뿐이다. 그래도 되돌릴 수 없는 작업이므로
    '이관이 실제로 끝났다'는 증거를 한 번 더 확인한다 — match_notes에 있던 만큼이
    활동 댓글 테이블에 들어와 있어야 한다. 하나라도 어긋나면 지우지 않고 그대로 둔다.

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
            text(f"SELECT COUNT(*) FROM {_COMMENTS} WHERE target_type = 'gameResult'")
        ) or 0
        if legacy > moved:
            logging.getLogger(__name__).warning(
                "match_notes(%s건)가 %s(%s건)보다 많아 옛 테이블을 남겨 둔다", legacy, _COMMENTS, moved,
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


async def _drop_member_epithets(conn: object) -> None:
    """칭호 체계 폐지(요청: 칭호 삭제, 요약 개념 자체 폐지) — 저장 테이블과 알림 행을
    걷어낸다(멱등). 규칙 본체(statEpithet.ts)와 API는 코드에서 이미 사라졌다."""
    import logging

    from sqlalchemy import text

    try:
        await conn.execute(text("DROP TABLE IF EXISTS member_epithets"))  # type: ignore[attr-defined]
        await conn.execute(text("DELETE FROM activity_notices WHERE kind = 'epithet'"))  # type: ignore[attr-defined]
        logging.getLogger(__name__).info("member_epithets 테이블·칭호 알림 삭제 완료")
    except Exception:  # noqa: BLE001 — 실패해도 부팅은 막지 않는다.
        logging.getLogger(__name__).warning("칭호 정리 건너뜀", exc_info=True)
