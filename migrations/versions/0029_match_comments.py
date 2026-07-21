"""경기 댓글(메모) — matches.note 한 필드에 마지막 메모만 덮어쓰던 방식을 버리고, 게시판
댓글처럼 (작성자, 본문 최대 50자) 여러 건을 쌓고 본인/운영자가 수정·삭제할 수 있는 정식
댓글 구조로 바꾼다(요청). match_comments + 언급(@) 저장용 match_comment_mentions 두 테이블을
새로 만들고 기존 note 컬럼은 지운다(데이터 무관 — 요청).

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-21

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "match_comments",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("match_id", sa.BigInteger(), sa.ForeignKey("matches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("members.pk", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), sa.ForeignKey("members.pk", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_match_comments_match_id", "match_comments", ["match_id"])

    op.create_table(
        "match_comment_mentions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("comment_id", sa.BigInteger(), sa.ForeignKey("match_comments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_pk", sa.BigInteger(), sa.ForeignKey("members.pk", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("comment_id", "member_pk", name="uq_match_comment_mentions_comment_member"),
    )

    op.drop_column("matches", "note")


def downgrade() -> None:
    op.add_column(
        "matches",
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
    )
    op.drop_table("match_comment_mentions")
    op.drop_index("ix_match_comments_match_id", table_name="match_comments")
    op.drop_table("match_comments")
