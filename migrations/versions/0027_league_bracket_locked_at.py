"""대진(시드) 확정 스위치를 추가한다(요청: "대진 확정 버튼을 추가해주고 그걸 누르면
그때부터 시드는 변경 못하게... 그전엔 부전승팀도 수정 가능해야해"). NULL이면 1라운드
시드를 자유롭게 바꿀 수 있고(부전승으로 이미 결정된 자리 포함), 값이 있으면 잠긴다.

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-21

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leagues", sa.Column("bracket_locked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("leagues", "bracket_locked_at")
