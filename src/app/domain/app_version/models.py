from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, SmallInteger, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# 새 DB(테스트의 create_all 등)나 아직 시드가 안 된 상태에서 최소한 이만큼은 고를 수 있게
# 하는 기본 버전 목록 — 운영 DB는 마이그레이션(0017)이 같은 값을 시드한다. 새 버전은 시드
# 마이그레이션으로 한 행씩 늘린다(요청: "버전은 테이블에 관리, 숫자 3.1 이런식").
SEED_VERSIONS = ["1", "2", "3"]


class AppVersionState(Base):
    """전체 서비스가 지금 보여주는 화면/메뉴 구성이 몇 버전인지 — 싱글턴 행(id=1) 하나만
    존재한다. active_version은 app_versions에 등록된 버전 숫자(예: "3", "3.1") 중 하나다.
    관리자 아무나(운영자) 관리자 패널에서 등록된 버전으로 바로 배포할 수 있다(합의 절차 없음)."""

    __tablename__ = "app_version_state"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    active_version: Mapped[str] = mapped_column(String(8), nullable=False, default="1")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AppVersionEntry(Base):
    """배포/미리보기로 고를 수 있는 '등록된' 버전 하나 — 관리자 패널의 버전 선택 팝업이 이
    표를 그대로 나열한다. 숫자(정수 또는 소수 한 단계, 예: "3", "3.1")로 구성된다."""

    __tablename__ = "app_versions"

    # SmallInteger PK는 SQLite에서 자동증가가 안 돼(테스트 create_all) — 다른 테이블과 같이
    # BigInteger(+SQLite Integer 변형)로 두어 자동증가되게 한다.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    number: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    # 이 버전이 배포된 뒤 처음 접속하는 회원에게 한 번 보여줄 "업데이트 안내" 내용 —
    # 한 줄에 한 항목(줄바꿈으로 구분)으로 관리자 패널의 "버전 안내 설정"에서 편집한다.
    # 예전엔 프론트 상수(APP_UPDATE_NOTES) 한 벌을 전 버전 공용으로 썼는데, 버전별로
    # 달리 쓰고 코드 배포 없이 바꿀 수 있게 DB(버전 행)로 옮겼다. 비어 있으면 그 버전은
    # 안내를 띄우지 않는다.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
