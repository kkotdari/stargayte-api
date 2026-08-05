from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import BigIntPk

# 화면 코드 화이트리스트는 schemas.py의 ScreenCode 하나만 쓴다 — 예전엔 여기 DB CHECK
# 제약이 같은 목록을 이중으로 들고 있었는데, 이 프로젝트는 스키마를 create_all로만 관리해
# (마이그레이션 없음) 기존 DB의 제약이 영원히 갱신되지 않는다. 그래서 화면이 추가될 때마다
# 코드에는 반영돼도 운영 DB 제약에는 없어서, 그 화면 진입 기록이 INSERT 단계에서 조용히
# 터지는 사고가 반복됐다(challenge/gameId → leagues/rivalry → feed). 검증은 API 계층
# (Pydantic ScreenCode)이 이미 확실히 하므로 DB 제약은 걷어낸다.
class AccessHistory(Base):
    __tablename__ = "access_history"

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
    # 그 화면 안에서 정확히 무엇을 봤는지 — 지금은 공유 링크로 열린 카드만 적는다
    # (screen_code="share"일 때 "gameResult#12" 같은 꼴). 그 외 화면은 NULL.
    detail: Mapped[str | None] = mapped_column(String(64), nullable=True)


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
