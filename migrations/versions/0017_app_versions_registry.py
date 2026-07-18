"""버전을 테이블(app_versions)로 관리한다 — 배포/미리보기로 고를 수 있는 '등록된 버전'
목록을 담고, 숫자(정수 또는 소수, 예: "3", "3.1")로 구성한다.

기존엔 active_version이 "v3"처럼 'v'+정수였고 배포/롤백이 +1/-1 증감이었는데, 이제
등록된 버전 중에서 고르는 방식으로 바꾼다(요청: "버전은 테이블에 관리, 제어판에서 등록된
버전만"). 그래서 (1) app_versions 레지스트리 테이블을 만들고 지금까지의 정수 버전(1,2,3)을
시드하며, (2) 싱글턴 상태의 active_version 값을 "vN" → "N"으로 변환한다.

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-18

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEED_VERSIONS = ["1", "2", "3"]


def upgrade() -> None:
    op.create_table(
        "app_versions",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("number", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("number"),
    )
    versions = sa.table("app_versions", sa.column("number", sa.String))
    op.bulk_insert(versions, [{"number": n} for n in _SEED_VERSIONS])
    # 기존 active_version "vN" → "N" (앞의 'v' 제거). 이미 숫자면 건드리지 않는다.
    op.execute(
        "UPDATE app_version_state SET active_version = substr(active_version, 2) "
        "WHERE active_version LIKE 'v%'"
    )


def downgrade() -> None:
    # active_version을 다시 "vN"으로 되돌린다(숫자로 저장된 값 앞에 'v'를 붙인다).
    op.execute(
        "UPDATE app_version_state SET active_version = 'v' || active_version "
        "WHERE active_version NOT LIKE 'v%'"
    )
    op.drop_table("app_versions")
