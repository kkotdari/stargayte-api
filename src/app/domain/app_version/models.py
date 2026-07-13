from datetime import datetime

from sqlalchemy import DateTime, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AppVersionState(Base):
    """전체 서비스가 지금 보여주는 화면/메뉴 구성이 몇 버전(v1, v2, v3, ...)인지 — 싱글턴
    행(id=1) 하나만 존재한다. 버전은 계속 늘어나는 걸 전제로 하며(상한 없음), 관리자
    아무나(운영자) 관리자 패널에서 바로 배포(+1)/롤백(-1)할 수 있다(합의 절차 없음)."""

    __tablename__ = "app_version_state"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    active_version: Mapped[str] = mapped_column(String(8), nullable=False, default="v1")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
