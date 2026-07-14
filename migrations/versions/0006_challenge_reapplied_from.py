"""challenges.reapplied_from_id 추가 — 재신청은 이제 기존 행을 고쳐 쓰지 않고 새 행을
만든다. 이 컬럼이 그 새 행이 어느 도전장에서 이어졌는지를 가리킨다(자기 참조 FK).

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-14

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("challenges", sa.Column("reapplied_from_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_challenges_reapplied_from_id",
        "challenges", "challenges",
        ["reapplied_from_id"], ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_challenges_reapplied_from_id", "challenges", type_="foreignkey")
    op.drop_column("challenges", "reapplied_from_id")
