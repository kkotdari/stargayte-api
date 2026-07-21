"""개인리그는 팀이 아니라 선수 1명 단위라 훨씬 많은 인원이 참가할 수 있어야 한다(요청:
"개인전은 최대 24명까지 가능하게") — planned_teams의 CHECK 상한을 6에서 24로 넓힌다.
정확한 모드별 상한(팀리그 6/개인리그 24)은 서비스 레이어에서 검증한다.

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-21

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_leagues_planned_teams_range", "leagues", type_="check")
    op.create_check_constraint(
        "ck_leagues_planned_teams_range",
        "leagues",
        "planned_teams IS NULL OR (planned_teams >= 2 AND planned_teams <= 24)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_leagues_planned_teams_range", "leagues", type_="check")
    op.create_check_constraint(
        "ck_leagues_planned_teams_range",
        "leagues",
        "planned_teams IS NULL OR (planned_teams >= 2 AND planned_teams <= 6)",
    )
