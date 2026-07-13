from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EnvVar(Base):
    """관리자 패널 잠금 비밀번호처럼, 코드 배포 없이 DB에서 바로 바꾸고 싶은 값을 담아두는
    아주 단순한 key-value 표 — 컬럼은 key/value 둘뿐이다."""

    __tablename__ = "env_vars"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
