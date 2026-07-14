"""challenges에 결과(승자 측)/결과입력자/체인 종류 컬럼 추가.

확정된 대결의 승부를 참가자가 직접 입력하도록(먼저 입력하는 쪽 인정) result_winner_side/
result_entered_by/result_entered_at을 추가하고, reapplied_from_id로 이어진 체인이
재신청("reapply")인지 설욕전("revenge")인지 구분하는 chain_kind를 추가한다.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-15

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("challenges", sa.Column("chain_kind", sa.String(length=10), nullable=True))
    op.add_column("challenges", sa.Column("result_winner_side", sa.String(length=10), nullable=True))
    op.add_column("challenges", sa.Column("result_entered_by", sa.BigInteger(), nullable=True))
    op.add_column(
        "challenges", sa.Column("result_entered_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_challenges_result_entered_by",
        "challenges", "members",
        ["result_entered_by"], ["pk"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_challenges_chain_kind", "challenges", "chain_kind IN ('reapply','revenge')",
    )
    op.create_check_constraint(
        "ck_challenges_result_winner_side", "challenges", "result_winner_side IN ('creator','target')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_challenges_result_winner_side", "challenges", type_="check")
    op.drop_constraint("ck_challenges_chain_kind", "challenges", type_="check")
    op.drop_constraint("fk_challenges_result_entered_by", "challenges", type_="foreignkey")
    op.drop_column("challenges", "result_entered_at")
    op.drop_column("challenges", "result_entered_by")
    op.drop_column("challenges", "result_winner_side")
    op.drop_column("challenges", "chain_kind")
