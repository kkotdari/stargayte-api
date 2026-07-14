"""challenges.photo_url 제거 — 도전장 사진 첨부 기능을 완전히 없앤다.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-14

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("challenges", "photo_url")


def downgrade() -> None:
    op.add_column("challenges", sa.Column("photo_url", sa.Text(), nullable=True))
