""""대결 요청" 코너 — 너 나와! 화면 최상단에 쌓이는 공개 요청글. 본문(text)에 @태그로 최소
2명을 지목(match_request_targets)하고, 다른 회원들이 추천(match_request_recommends)을 누를
수 있다. 지목된 사람만 "들어주기"로 도전장을 보낼 수 있고, 들어주면 fulfilled_at으로 목록에서
사라진다.

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-19

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "match_requests",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("members.pk", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), sa.ForeignKey("members.pk", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_match_requests_fulfilled_at", "match_requests", ["fulfilled_at"])

    op.create_table(
        "match_request_targets",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("request_id", sa.BigInteger(), sa.ForeignKey("match_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_pk", sa.BigInteger(), sa.ForeignKey("members.pk", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("request_id", "member_pk", name="uq_match_request_targets_request_member"),
    )

    op.create_table(
        "match_request_recommends",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("request_id", sa.BigInteger(), sa.ForeignKey("match_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_pk", sa.BigInteger(), sa.ForeignKey("members.pk", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("members.pk", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), sa.ForeignKey("members.pk", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("request_id", "member_pk", name="uq_match_request_recommends_request_member"),
    )


def downgrade() -> None:
    op.drop_table("match_request_recommends")
    op.drop_table("match_request_targets")
    op.drop_index("ix_match_requests_fulfilled_at", table_name="match_requests")
    op.drop_table("match_requests")
