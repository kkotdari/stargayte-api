"""도전장에 from_match_request 플래그 추가 — "대결 요청 코너"의 요청을 "들어주기"로 받아 만든
도전장에 "요청대결" 배지를 붙이기 위한 표식(요청). 일반 도전장은 False.

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-19

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "challenges",
        sa.Column("from_match_request", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("challenges", "from_match_request")
