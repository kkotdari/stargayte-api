"""challenge_participants.reject_reason -> response_message — 도전장 응답(수락/거절)에
남기는 한마디를 거절 전용이 아니라 양쪽 모두에서 쓰도록 컬럼 이름을 일반화한다.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-14

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "challenge_participants", "reject_reason", new_column_name="response_message",
    )


def downgrade() -> None:
    op.alter_column(
        "challenge_participants", "response_message", new_column_name="reject_reason",
    )
