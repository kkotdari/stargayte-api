"""기존 도전장 중 이미 응답 마감이 지난 pending 건을 무응답거절로 일괄 확정한다.

런타임에서는 너나와 목록을 조회할 때마다 배치(ChallengeService._expire_stale_challenges)가
같은 처리를 하지만, 그건 "누가 목록을 볼 때"에 의존한다 — 배포 시점에 이미 마감이 지나
있던 기존 건들도 곧바로 정리해두기 위해 같은 규칙을 데이터 마이그레이션으로 한 번 돌린다
(요청: "기존건들도 무응답 종료된건은 마이그레이션 해야돼").

무응답거절 = 마감(예정 일시가 있으면 그 시각, 없으면 created_at + 1일)이 지났는데도 아직
pending인 도전장의, 응답 안 한 지목자를 rejected(메시지 없음)로 바꾼다. 다루는 방식은
거절과 같고 한마디만 없다. 취소됐거나(canceled_at) 이미 누가 거절한(=rejected 지목자가
있는) 건은 건드리지 않는다 — 런타임 배치와 완전히 같은 조건이다.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-15

"""
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 런타임 REAPPLY_EXPIRE(service.py)와 같은 값 — 시간 미정 도전장의 응답 기한.
_RESPONSE_EXPIRE = timedelta(days=1)


def upgrade() -> None:
    now = datetime.now(UTC)
    day_ago = now - _RESPONSE_EXPIRE
    bind = op.get_bind()
    # 마감(요청일+1일)이 지난 pending 도전장을 찾는다 — canceled 아님, 거절자 없음, 미응답
    # 지목자 있음(=아직 pending). 상관 UPDATE는 방언차가 커서 SELECT 후 행별로 처리한다.
    rows = bind.execute(
        sa.text(
            """
            SELECT c.id, c.created_at, c.scheduled_at FROM challenges c
            WHERE c.canceled_at IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM challenge_participants r
                WHERE r.challenge_id = c.id AND r.side = 'target' AND r.response = 'rejected'
              )
              AND EXISTS (
                SELECT 1 FROM challenge_participants p
                WHERE p.challenge_id = c.id AND p.side = 'target' AND p.response = 'pending'
              )
              AND c.created_at < :day_ago
            """
        ).bindparams(day_ago=day_ago)
    ).fetchall()

    for row in rows:
        cid, created_at, scheduled_at = row[0], row[1], row[2]
        # 미응답 지목자 → 무응답거절(메시지 없음).
        bind.execute(
            sa.text(
                "UPDATE challenge_participants SET response = 'rejected', "
                "response_message = NULL, responded_at = :now "
                "WHERE challenge_id = :cid AND side = 'target' AND response = 'pending'"
            ).bindparams(now=now, cid=cid)
        )
        # 시간 미정이었으면 예정 일시를 요청일+1일로 스탬프한다("scheduled_at 없음 = 아직
        # 응답 대기중"이 성립하도록 — 런타임 _stamp_schedule_on_end와 같은 처리).
        if scheduled_at is None:
            bind.execute(
                sa.text("UPDATE challenges SET scheduled_at = :val WHERE id = :cid").bindparams(
                    val=created_at + _RESPONSE_EXPIRE, cid=cid
                )
            )


def downgrade() -> None:
    # 무응답거절(자동)과 사람이 직접 한 거절을 사후에 구분할 근거가 없어(둘 다 rejected)
    # 되돌리지 않는다 — 데이터 확정 마이그레이션이라 downgrade는 무연산이다.
    pass
