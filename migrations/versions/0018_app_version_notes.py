"""버전별 "업데이트 안내" 내용을 DB(app_versions.notes)로 옮긴다.

예전엔 프론트 상수(APP_UPDATE_NOTES) 한 벌을 전 버전 공용으로 썼는데, 이제 관리자 패널의
"버전 안내 설정"에서 버전을 골라 코드 배포 없이 내용을 편집할 수 있게 버전 행에 notes(Text)
컬럼을 붙인다. 기존 프론트 상수의 내용은 사라지지 않게 지금의 최신 버전("3")에 그대로
시드한다. 안내 표시 전역 on/off 토글은 env_vars(version_notice_enabled)에서 관리하며, 행이
없으면 켜짐으로 본다(앱 기본값) — 그래서 여기선 따로 시드하지 않는다.

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-18

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 지금까지 프론트 상수(APP_UPDATE_NOTES)로 보여주던 내용 — 최신 버전에 그대로 옮겨 담아
# 기존 안내가 유실되지 않게 한다. 한 줄에 한 항목(줄바꿈으로 구분).
_SEED_NOTES_VERSION = "3"
_SEED_NOTES = "\n".join(
    [
        "랭킹이 일대일/팀으로 나뉘었어요.",
        "챌린지 코너가 새로 생겼어요 — 원하는 상대를 지목해 대결을 신청해보세요!",
    ]
)


def upgrade() -> None:
    op.add_column("app_versions", sa.Column("notes", sa.Text(), nullable=True))
    op.execute(
        sa.text("UPDATE app_versions SET notes = :notes WHERE number = :number").bindparams(
            notes=_SEED_NOTES, number=_SEED_NOTES_VERSION
        )
    )


def downgrade() -> None:
    op.drop_column("app_versions", "notes")
