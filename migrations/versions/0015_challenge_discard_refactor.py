"""도전장 상태를 4개(응답대기/성사/완료/폐기)로 단순화한다. 취소/연기 기능이 제거되고
거절·무응답·미실시·(레거시)취소가 모두 "폐기(휴지통)"로 통합됐다.

- challenges.discarded_at(폐기 시각), deleted_at(휴지통 7일 소프트삭제) 추가.
- 기존 폐기 대상(취소/명시적 거절+무응답 거절/미실시)에 discarded_at 백필.
- 레거시 재신청(reapply) 체인은 언체인 → 독립 신규건으로(요청). 재대결(revenge)만 남는다.
- 이제 불필요한 canceled_at, chain_kind 컬럼 삭제.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-16

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("challenges", sa.Column("discarded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("challenges", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))

    # 기존 폐기 대상에 discarded_at 백필 — 이미 채워진 건은 건드리지 않도록 매번 IS NULL 가드.
    # (1) 취소: 취소 시각을 그대로 폐기 시각으로.
    op.execute(
        "UPDATE challenges SET discarded_at = canceled_at "
        "WHERE canceled_at IS NOT NULL AND discarded_at IS NULL"
    )
    # (2) 미실시(not_held): 결과 입력 시각(없으면 갱신/생성 시각).
    op.execute(
        "UPDATE challenges SET discarded_at = COALESCE(result_entered_at, updated_at, created_at) "
        "WHERE result_winner_side = 'not_held' AND discarded_at IS NULL"
    )
    # (3) 명시적 거절 + 무응답 거절(옛 배치가 response='rejected'로 확정해둔 것 포함):
    #     지목자 중 거절이 하나라도 있으면 폐기. 시각은 갱신/생성 시각으로 근사.
    op.execute(
        "UPDATE challenges SET discarded_at = COALESCE(updated_at, created_at) "
        "WHERE discarded_at IS NULL AND id IN ("
        "  SELECT challenge_id FROM challenge_participants "
        "  WHERE side = 'target' AND response = 'rejected'"
        ")"
    )

    # 레거시 재신청(reapply) 체인은 독립 신규건으로 끊는다(요청: "휴지통에 들어갈 도전장에
    # 체이닝한 경우 신규 초대건으로"). 재신청은 항상 거절/취소(=폐기) 뒤에 생긴 것이라, 그
    # 부모가 폐기됐다. 부모를 가리키던 링크를 끊으면 자식은 독립 신규 초대가 되고, 폐기된
    # 부모는 휴지통에 남는다. 재대결(revenge) 체인은 완료 건에서 이어진 것이라 그대로 둔다.
    op.execute(
        "UPDATE challenges SET reapplied_from_id = NULL, chain_kind = NULL "
        "WHERE chain_kind = 'reapply'"
    )

    # 이제 불필요한 컬럼/제약 삭제.
    op.drop_constraint("ck_challenges_chain_kind", "challenges", type_="check")
    op.drop_column("challenges", "chain_kind")
    op.drop_column("challenges", "canceled_at")


def downgrade() -> None:
    op.add_column("challenges", sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("challenges", sa.Column("chain_kind", sa.String(length=10), nullable=True))
    op.create_check_constraint(
        "ck_challenges_chain_kind", "challenges", "chain_kind IN ('reapply','revenge')"
    )
    # 폐기 시각만으로는 원래 취소였는지 거절/미실시였는지 구분할 수 없어, 취소값은 복원하지
    # 않는다(best-effort). 새 컬럼만 제거한다.
    op.drop_column("challenges", "deleted_at")
    op.drop_column("challenges", "discarded_at")
