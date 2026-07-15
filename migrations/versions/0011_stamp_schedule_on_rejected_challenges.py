"""거절 상태인데 예정 일시가 없는 도전장을 요청일+1일로 스탬프한다.

0010은 "무응답(pending 만료)"만 처리했고, 사람이 직접 "거절"을 눌러 이미 rejected가 된
기존 건들(예정 일시 없이 보낸 것)은 NOT EXISTS(rejected) 조건에 걸려 제외돼 스탬프가 안
됐다 — 그래서 화면에서 계속 "일정 미정"으로 떴다(요청: "왜 거절/무응답 거절 건중 아직도
일정미정이라고 뜨는게 있지"). 런타임 배치(_expire_stale_challenges)도 이제 rejected+예정
일시 없음이면 스탬프하도록 고쳤지만, 배포 시점에 한 번 확실히 정리하기 위해 같은 처리를
데이터 마이그레이션으로도 돌린다. 취소된 건은 화면에 안 보이므로 건드리지 않는다.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-16

"""
from collections.abc import Sequence
from datetime import timedelta

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RESPONSE_EXPIRE = timedelta(days=1)


def upgrade() -> None:
    bind = op.get_bind()
    # 거절(지목자 중 rejected가 있음) + 예정 일시 없음 + 미취소인 도전장.
    rows = bind.execute(
        sa.text(
            """
            SELECT c.id, c.created_at FROM challenges c
            WHERE c.canceled_at IS NULL AND c.scheduled_at IS NULL
              AND EXISTS (
                SELECT 1 FROM challenge_participants r
                WHERE r.challenge_id = c.id AND r.side = 'target' AND r.response = 'rejected'
              )
            """
        )
    ).fetchall()
    for row in rows:
        cid, created_at = row[0], row[1]
        bind.execute(
            sa.text("UPDATE challenges SET scheduled_at = :val WHERE id = :cid").bindparams(
                val=created_at + _RESPONSE_EXPIRE, cid=cid
            )
        )


def downgrade() -> None:
    # 자동 스탬프값과 사용자가 실제로 잡은 시각을 사후에 구분할 수 없어 되돌리지 않는다.
    pass
