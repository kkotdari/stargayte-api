"""대결 요청 개편 — @태그 기능 폐지. 언급된 사람(match_request_targets)은 표시 + 알림
대상으로만 남기고, 알림 읽음 상태를 위해 read_at 컬럼을 추가한다. 요청이 등록되면 언급된
회원에게 알림이 되고(read_at NULL=안읽음, 앱 열 때 인박스 팝업), 한 번 읽으면 read_at을 채운다.

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-19

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "match_request_targets",
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_match_request_targets_member_read",
        "match_request_targets",
        ["member_pk", "read_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_match_request_targets_member_read", table_name="match_request_targets")
    op.drop_column("match_request_targets", "read_at")
