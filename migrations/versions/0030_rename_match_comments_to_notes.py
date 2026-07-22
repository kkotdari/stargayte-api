"""경기 메모 테이블 이름을 comment → note로 변경(요청). 개념이 '댓글'이 아니라 '각자의 메모'라
match_comments → match_notes, match_comment_mentions → match_note_mentions로 바꾸고, 멘션의
comment_id 컬럼·인덱스·유니크 제약 이름도 note 기준으로 맞춘다. 데이터는 그대로 보존된다
(테이블/컬럼 rename이라 내용 이동 없음).

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-22

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("match_comments", "match_notes")
    op.rename_table("match_comment_mentions", "match_note_mentions")
    op.alter_column("match_note_mentions", "comment_id", new_column_name="note_id")
    op.execute("ALTER INDEX ix_match_comments_match_id RENAME TO ix_match_notes_match_id")
    op.execute(
        "ALTER TABLE match_note_mentions "
        "RENAME CONSTRAINT uq_match_comment_mentions_comment_member "
        "TO uq_match_note_mentions_note_member"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE match_note_mentions "
        "RENAME CONSTRAINT uq_match_note_mentions_note_member "
        "TO uq_match_comment_mentions_comment_member"
    )
    op.execute("ALTER INDEX ix_match_notes_match_id RENAME TO ix_match_comments_match_id")
    op.alter_column("match_note_mentions", "note_id", new_column_name="comment_id")
    op.rename_table("match_note_mentions", "match_comment_mentions")
    op.rename_table("match_notes", "match_comments")
