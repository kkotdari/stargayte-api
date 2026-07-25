"""이미지 설정(종족 아이콘 커스텀) 기능 제거 — image_settings 테이블 삭제.

프론트의 이미지 설정 메뉴/컨텍스트와 함께 백엔드 도메인(app/domain/settings)도
통째로 걷어냈다(요청: "이미지 설정 메뉴 완전 삭제 및 사용처 코드 모두 제거").
종족 표시는 이제 코드에 박힌 기본(T/P/Z/R 글자)만 쓴다.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("image_settings")


def downgrade() -> None:
    # 0001 당시 스키마 그대로 복원 — 데이터(운영자가 넣었던 아이콘)는 되살릴 수 없다.
    op.create_table(
        "image_settings",
        sa.Column("slot", sa.String(length=30), nullable=False),
        sa.Column("icon_type", sa.String(length=10), nullable=False),
        sa.Column("icon_value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["members.pk"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["members.pk"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("slot"),
    )
