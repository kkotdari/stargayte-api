"""도전장(너 나와!) '한마디' 복원 — 호출 한마디(challenges.message)와 응답 한마디
(challenge_participants.response_message) 두 컬럼을 다시 추가한다(요청). 예전에 이 개념을
통째로 지웠는데(0022), 이번엔 카드에 함께 보여줄 호출/응답 한마디로 되살린다. 기존 데이터는
복구할 수 없어 빈 문자열로 시작한다.

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-22

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "challenges",
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "challenge_participants",
        sa.Column("response_message", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("challenge_participants", "response_message")
    op.drop_column("challenges", "message")
