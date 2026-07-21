"""팀/선수/대진표 규모를 무제한으로 푼다(요청: "팀수 무제한 개인전 선수 무제한 대진표
슬롯 무제한"). planned_teams의 상한(24)을 제거하고(하한 2만 유지), 팀 라벨이 26개
(Z)를 넘어가면 스프레드시트 열 이름 방식(AA, AB, ...)으로 여러 글자가 되므로 label
컬럼을 VARCHAR(1)에서 VARCHAR(4)로 넓힌다.

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-21

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_leagues_planned_teams_range", "leagues", type_="check")
    op.create_check_constraint(
        "ck_leagues_planned_teams_range",
        "leagues",
        "planned_teams IS NULL OR planned_teams >= 2",
    )
    op.alter_column(
        "league_teams", "label",
        existing_type=sa.String(length=1),
        type_=sa.String(length=4),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "league_teams", "label",
        existing_type=sa.String(length=4),
        type_=sa.String(length=1),
        existing_nullable=False,
    )
    op.drop_constraint("ck_leagues_planned_teams_range", "leagues", type_="check")
    op.create_check_constraint(
        "ck_leagues_planned_teams_range",
        "leagues",
        "planned_teams IS NULL OR (planned_teams >= 2 AND planned_teams <= 24)",
    )
