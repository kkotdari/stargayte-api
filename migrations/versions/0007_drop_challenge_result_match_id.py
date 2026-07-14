"""challenges.result_match_id 제거 — 도전장을 실제 경기결과와 연결하는 기능(attach-result)
자체를 없앴다. "결과 보기"는 이제 팀 구성/날짜로 경기 목록을 찾아 보여줄 뿐, 이 컬럼과
무관하게 동작한다.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-14

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0001_initial_schema가 이 FK를 이름 없이(op.create_table 안 ForeignKeyConstraint)
    # 만들어서 실제 제약 이름이 DB가 붙인 기본값에 달려 있다 — 하드코딩 대신 인스펙터로
    # 찾아서 지운다.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for fk in inspector.get_foreign_keys("challenges"):
        if fk.get("constrained_columns") == ["result_match_id"]:
            op.drop_constraint(fk["name"], "challenges", type_="foreignkey")
    op.drop_column("challenges", "result_match_id")


def downgrade() -> None:
    op.add_column("challenges", sa.Column("result_match_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "challenges_result_match_id_fkey",
        "challenges", "matches",
        ["result_match_id"], ["id"],
        ondelete="SET NULL",
    )
