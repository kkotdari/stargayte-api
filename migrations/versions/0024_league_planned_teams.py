"""리그 대진표를 "몇 팀짜리로 잡을지" 미리 정하는 planned_teams 컬럼 추가 — 실제 참가
팀 수와 별개로 대진표를 먼저 생성할 수 있게 한다(요청: "대진표는 팀이 있건 없건 생성
가능하게, 팀수 미리 설정 가능").

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-21

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leagues", sa.Column("planned_teams", sa.SmallInteger(), nullable=True))
    op.create_check_constraint(
        "ck_leagues_planned_teams_range",
        "leagues",
        "planned_teams IS NULL OR (planned_teams >= 2 AND planned_teams <= 6)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_leagues_planned_teams_range", "leagues", type_="check")
    op.drop_column("leagues", "planned_teams")
