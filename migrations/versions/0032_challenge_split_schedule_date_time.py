"""도전장(너 나와!) 일정을 날짜/시간 두 컬럼으로 분리 — scheduled_at(단일 timestamptz)을
scheduled_date(Date) + scheduled_time(Time)로 쪼갠다(요청: "시간은 null 가능으로", "날짜만
지정하고 시간은 나중에 결정하는 경우가 많다"). 둘 다 한국시간 벽시계값(naive)으로 보관한다.

기존 scheduled_at(UTC timestamptz)은 한국시간으로 환산해 날짜/시간으로 나눠 담는다. 시간이
자정이었던(예전 "시간 미정" 스탬프) 행도 그대로 옮겨지지만, 앞으로 시간 미정은 scheduled_time
NULL로 표현된다.

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-24

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("challenges", sa.Column("scheduled_date", sa.Date(), nullable=True))
    op.add_column("challenges", sa.Column("scheduled_time", sa.Time(), nullable=True))
    # 기존 UTC timestamptz를 한국시간 벽시계값으로 환산해 날짜/시간으로 분리한다.
    op.execute(
        sa.text(
            "UPDATE challenges "
            "SET scheduled_date = (scheduled_at AT TIME ZONE 'Asia/Seoul')::date, "
            "    scheduled_time = (scheduled_at AT TIME ZONE 'Asia/Seoul')::time "
            "WHERE scheduled_at IS NOT NULL"
        )
    )
    op.drop_column("challenges", "scheduled_at")


def downgrade() -> None:
    op.add_column(
        "challenges",
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 날짜(+시간, 없으면 자정)를 한국시간으로 해석해 다시 UTC timestamptz로 합친다.
    op.execute(
        sa.text(
            "UPDATE challenges "
            "SET scheduled_at = (scheduled_date + COALESCE(scheduled_time, '00:00'::time)) "
            "    AT TIME ZONE 'Asia/Seoul' "
            "WHERE scheduled_date IS NOT NULL"
        )
    )
    op.drop_column("challenges", "scheduled_time")
    op.drop_column("challenges", "scheduled_date")
