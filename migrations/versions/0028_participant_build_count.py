"""match_participants.build_count 추가 — 리플레이 커맨드 스트림에서 센 '생산' 지표
(유닛 훈련+건물 건설+변태 커맨드 수). apm/eapm/cmd_count처럼 리플레이 파싱으로만
채워지고 수동 등록·과거 데이터는 NULL이다.

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-21

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("match_participants", sa.Column("build_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("match_participants", "build_count")
