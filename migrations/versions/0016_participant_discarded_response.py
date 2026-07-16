"""지목된 상대의 응답에 'discarded'(사유 없이 버림, 휴지통행)를 추가한다.

편지봉투를 열지 않고 "버리기"를 누르면, 사유가 있는 명시적 "rejected"(거절)와 구분되는
"discarded"(버림)로 기록된다(도전장 자체는 두 경우 모두 폐기/휴지통으로 간다). 여기서는
challenge_participants.response 체크 제약에 'discarded'만 허용값으로 추가한다.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-16

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CK = "ck_challenge_participants_response"
_TABLE = "challenge_participants"


def upgrade() -> None:
    op.drop_constraint(_CK, _TABLE, type_="check")
    op.create_check_constraint(
        _CK, _TABLE, "response IN ('pending','accepted','rejected','discarded')"
    )


def downgrade() -> None:
    # 되돌리기 전, 'discarded'로 남은 응답은 예전 허용값인 'rejected'로 흡수해야 제약을
    # 다시 걸 때 위반이 없다(둘 다 도전장은 폐기 상태라 의미상 큰 손실은 없다).
    op.execute("UPDATE challenge_participants SET response = 'rejected' WHERE response = 'discarded'")
    op.drop_constraint(_CK, _TABLE, type_="check")
    op.create_check_constraint(
        _CK, _TABLE, "response IN ('pending','accepted','rejected')"
    )
