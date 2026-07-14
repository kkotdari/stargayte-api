"""challenge_participants.result_notified 추가 — "결과 입력" 팝업을 참가자별로 한 번만
보여주기 위한 서버 사이드 플래그(요청: "결과 입력 팝업 확인 여부는 디비에 관리").
초대 팝업의 notified와 같은 원리인데, 결과 입력은 양쪽 참가자 전원이 대상이라 side와
무관하게 모든 행에서 쓰인다.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-15

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 기존 행은 전부 "아직 안 봤음"으로 시작한다 — 예정이 지난 미입력 대결이 이미 있다면
    # 배포 직후 첫 접속 때 한 번 팝업이 뜨는 게 의도된 동작이다.
    op.add_column(
        "challenge_participants",
        sa.Column("result_notified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("challenge_participants", "result_notified")
