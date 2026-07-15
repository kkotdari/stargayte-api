"""대결 결과에 무승부(draw)/미실시(not_held)를 허용한다.

결과 입력을 "이긴 쪽 고르기"에서 "구성원 보고 승리팀 or 무승부/미실시 고르기"로 바꾸면서
(요청: "구성원이 노출되고 승리팀을 고르는게 좋을듯. 무승부나 미실시도 있게 해주고")
result_winner_side가 가질 수 있는 값이 creator/target → creator/target/draw/not_held로
늘었다. 컬럼 타입(String(10))은 'not_held'까지 들어가 그대로 두고, CHECK 제약만 교체한다.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-15

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_challenges_result_winner_side", "challenges", type_="check")
    op.create_check_constraint(
        "ck_challenges_result_winner_side",
        "challenges",
        "result_winner_side IN ('creator','target','draw','not_held')",
    )


def downgrade() -> None:
    # 되돌리면 draw/not_held 값이 제약에 걸린다 — 그 값들은 사후에 되살릴 수 없어(정보 손실)
    # 굳이 정리하지 않는다. 제약만 원래(creator/target)로 되돌린다.
    op.drop_constraint("ck_challenges_result_winner_side", "challenges", type_="check")
    op.create_check_constraint(
        "ck_challenges_result_winner_side",
        "challenges",
        "result_winner_side IN ('creator','target')",
    )
