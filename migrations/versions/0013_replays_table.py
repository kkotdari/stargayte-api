"""리플레이를 별도 replays 테이블에 풀 메타데이터로 저장하고, 경기는 matches.replay_id로
매핑한다(요청). 기존 match_attachments(리플레이 전용)는 드롭한다 — 데이터 마이그레이션은
하지 않는다(운영자가 리플레이 전체를 재등록).

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-15

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "replays",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("game_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("map_name", sa.String(length=150), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("members.pk", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), sa.ForeignKey("members.pk", ondelete="SET NULL"), nullable=True),
    )
    op.add_column("matches", sa.Column("replay_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_matches_replay_id", "matches", "replays", ["replay_id"], ["id"])
    op.create_unique_constraint("uq_matches_replay_id", "matches", ["replay_id"])
    op.drop_table("match_attachments")


def downgrade() -> None:
    op.create_table(
        "match_attachments",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("match_id", sa.BigInteger(), sa.ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("members.pk", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), sa.ForeignKey("members.pk", ondelete="SET NULL"), nullable=True),
    )
    op.drop_constraint("uq_matches_replay_id", "matches", type_="unique")
    op.drop_constraint("fk_matches_replay_id", "matches", type_="foreignkey")
    op.drop_column("matches", "replay_id")
    op.drop_table("replays")
