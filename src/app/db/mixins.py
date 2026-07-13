from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """created_at / updated_at 자동 관리 믹스인."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AuditMixin:
    """등록자/수정자(members.pk) 추적 믹스인.

    members.id(로그인 아이디)가 아니라 불변 pk를 참조하므로 회원이 나중에 아이디를
    바꿔도 과거 기록은 그대로 유효하다. 회원 탈퇴 등으로 계정이 사라져도 과거 기록
    자체는 남아야 하므로 RESTRICT가 아닌 SET NULL로 둔다.
    """

    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("members.pk", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("members.pk", ondelete="SET NULL"), nullable=True
    )
