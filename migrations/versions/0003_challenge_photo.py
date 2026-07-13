"""challenges.photo_url — 도전장 보내기 폼에 사진 첨부 기능 추가.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-14

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("challenges", sa.Column("photo_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("challenges", "photo_url")
