from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import AuditMixin


class ImageSetting(AuditMixin, Base):
    """관리자 설정 - 종족(테란/프로토스/저그/랜덤) 아이콘 + 홈 로고(다크/라이트) 이미지 슬롯.
    슬롯 하나당 정확히 한 행만 존재한다."""

    __tablename__ = "image_settings"

    slot: Mapped[str] = mapped_column(String(30), primary_key=True)
    icon_type: Mapped[str] = mapped_column(String(10), nullable=False)
    icon_value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
