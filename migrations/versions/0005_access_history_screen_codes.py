"""access_history.screen_code CHECK 제약에 challenge(너 나와!)/gameId(게임아이디)
추가 — 나중에 생긴 화면인데 초기 목록에 없어서 그 화면으로의 pingAccess가 검증
단계에서 조용히 막혔다.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-14

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_CODES = (
    "ranking", "match", "official", "stats", "members", "accessHistory",
    "imageSettings", "menuPermissions", "userMapping",
)
NEW_CODES = OLD_CODES + ("challenge", "gameId")


def _codes_sql(codes: tuple[str, ...]) -> str:
    return ", ".join("'{}'".format(c) for c in codes)


def upgrade() -> None:
    op.drop_constraint("ck_access_history_screen_code", "access_history", type_="check")
    op.create_check_constraint(
        "ck_access_history_screen_code",
        "access_history",
        f"screen_code IS NULL OR screen_code IN ({_codes_sql(NEW_CODES)})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_access_history_screen_code", "access_history", type_="check")
    op.create_check_constraint(
        "ck_access_history_screen_code",
        "access_history",
        f"screen_code IS NULL OR screen_code IN ({_codes_sql(OLD_CODES)})",
    )
