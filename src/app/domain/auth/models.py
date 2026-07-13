from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import BigIntPk

# 프론트엔드 ScreenKey(App.tsx)와 동일한 값만 허용 — 화면 종류는 프론트 코드가 고정하는
# 값이라 코드관리 화면처럼 관리자가 임의로 추가/편집할 이유가 없어서, 코드 테이블 대신
# DB 제약조건으로 못박아 둔다. 화면 이동 없이 발생하는 로그인 자체는 NULL로 남는다.
SCREEN_CODES = (
    "ranking", "match", "official", "stats", "members", "accessHistory",
    "imageSettings", "menuPermissions", "userMapping",
)


_SCREEN_CODES_SQL = ", ".join("'{}'".format(c) for c in SCREEN_CODES)


class AccessHistory(Base):
    __tablename__ = "access_history"
    __table_args__ = (
        CheckConstraint(
            f"screen_code IS NULL OR screen_code IN ({_SCREEN_CODES_SQL})",
            name="ck_access_history_screen_code",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    member_pk: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("members.pk", ondelete="CASCADE"), nullable=False
    )
    logged_in_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 어떤 화면(서비스)에서의 접속인지 — 로그인 자체(화면 이동 없이 발생)는 NULL.
    screen_code: Mapped[str | None] = mapped_column(String(32), nullable=True)


class RefreshToken(Base):
    """액세스 토큰(1시간) 만료 후 재로그인 없이 세션을 이어가기 위한 리프레시 토큰.
    사용할 때마다 새 토큰을 발급하고 기존 것은 폐기하는 로테이션 방식이라, 탈취된 토큰이
    재사용되면(이미 폐기된 토큰으로 다시 요청) 감지할 수 있다."""

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    member_pk: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("members.pk", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
