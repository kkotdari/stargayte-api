"""대결(도전장)의 '한마디' 개념 폐지 — 관련 컬럼을 삭제한다.

도전자의 한마디(challenges.message)와 응답 한마디(challenge_participants.response_message)를
모두 없앤다(요청: "대결에서 한마디 개념 모두 삭제, 기존 데이터도 컬럼 삭제, 아주 단순하게").
기존 데이터도 컬럼째 삭제되므로 남아 있던 메시지 텍스트는 사라진다.

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-20

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("challenges", "message")
    op.drop_column("challenge_participants", "response_message")


def downgrade() -> None:
    # 되돌리면 컬럼만 되살린다(내용은 이미 소실됐으므로 빈 값). message는 원래 NOT NULL
    # 기본 ''이었다 — 서버 기본값으로 채운 뒤 NOT NULL을 다시 건다.
    op.add_column(
        "challenges",
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
    )
    op.alter_column("challenges", "message", server_default=None)
    op.add_column(
        "challenge_participants",
        sa.Column("response_message", sa.Text(), nullable=True),
    )
