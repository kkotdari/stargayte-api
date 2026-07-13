from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import AuditMixin, TimestampMixin
from app.db.types import BigIntPk
from app.domain.members.models import Member


class Match(AuditMixin, TimestampMixin, Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    # 사람이 보고 지목하기 위한 고유번호 — 등록 순서(id)가 아니라 "그 경기가 실제로 언제
    # 열렸는지"를 기준으로 한다(리플레이는 한참 지나서야 등록되는 경우가 흔해서, id 순서가
    # 실제 경기 순서와 어긋난다). 형식: YYMMDDHHMMSS(리플레이가 있으면 실제 시작 시각(KST),
    # 없으면 경기 날짜 + 000000) + 2자리 일련번호(00부터, 같은 초/같은 날짜가 겹치면 01, 02...
    # 로 늘어난다 — 하루/한 초에 100건이 몰릴 일은 없다고 가정). service.py의 생성 로직
    # 참고. 한 번 배정되면 이후 수정에서도 절대 바뀌지 않는다.
    match_no: Mapped[str] = mapped_column(String(14), nullable=False, unique=True)
    match_date: Mapped[date] = mapped_column(Date, nullable=False)
    # 경기유형 코드 (0101=1:1, 0102=팀전). team1/team2 인원수와 별개로
    # 어떤 성격의 경기인지 분류하기 위한 값이라 컬럼으로 따로 관리한다.
    match_type: Mapped[str] = mapped_column(String(4), nullable=False, default="0101")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")

    participants: Mapped[list["MatchParticipant"]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
        order_by="MatchParticipant.position",
    )
    attachment: Mapped["MatchAttachment | None"] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
        uselist=False,
    )
    # 결과(승패/맵/시작시각/경기시간) — 얇은 사이드 테이블로 분리해 관리한다(모든 경기가
    # 등록과 동시에 결과를 함께 저장하므로 실질적으로 항상 1:1로 존재한다).
    result_row: Mapped["MatchResult | None"] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
        uselist=False,
    )
    # created_by는 AuditMixin이 제공하는 컬럼이라 이 클래스 본문에서 바로 이름을 못 쓰므로
    # 문자열로 지연 참조한다. 작성자 표시/삭제 권한 판단에 쓰고, 여기서 쓰지는 않는다(viewonly).
    creator: Mapped["Member | None"] = relationship(
        "Member", foreign_keys="Match.created_by", viewonly=True
    )


class MatchParticipant(AuditMixin, Base):
    __tablename__ = "match_participants"
    __table_args__ = (UniqueConstraint("match_id", "team", "position"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )
    team: Mapped[str] = mapped_column(String(5), nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # 실제 게임에서 쓰인 플레이어 이름(리플레이 파싱 원본 게임 아이디, 또는 수기등록 시
    # 고른 이름) — 절대 NULL이 될 수 없다(수기등록도 드롭다운에서 기존 이름을 고르거나,
    # 새 이름이면 회원/비회원/컴퓨터 중 하나로 즉시 분류해야만 등록이 끝난다). 회원
    # 여부/식별은 이 행에 저장하지 않고 매번 replay_aliases(raw_name → kind/member_pk)로
    # 조회해서 판단한다 — member_pk 컬럼을 따로 두면 회원이 여러 게임 아이디를 쓸 수 있는
    # 것과 이중 관리가 되어 어긋날 여지가 생긴다. 회원의 members.battletag는
    # 나중에 바뀔 수 있는 값이라, 그것만 믿으면 이 경기 시점에 실제로 어떤 게임 아이디로
    # 참가했는지 알 수 없게 된다 — 이 컬럼이 그 시점의 진짜 값을 영구 보존한다.
    player_name: Mapped[str] = mapped_column(String(100), nullable=False)
    race: Mapped[str] = mapped_column(String(20), nullable=False)
    # 아래 4개는 리플레이 파싱으로만 채워진다 (수동 등록 참가자는 항상 NULL).
    apm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eapm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cmd_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effective_cmd_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    match: Mapped[Match] = relationship(back_populates="participants")


class MatchAttachment(AuditMixin, Base):
    __tablename__ = "match_attachments"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("matches.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    match: Mapped[Match] = relationship(back_populates="attachment")


class MatchResult(Base):
    """경기 결과 — status가 completed로 확정된 경기에만 이 행이 존재한다(예약/취소 상태는
    애초에 결과가 없으므로 행 자체가 없다). 리플레이 메타데이터(맵/시작시각/경기시간)도
    "실제로 어떻게 끝났는가"에 속하는 정보라 matches가 아니라 여기로 옮겼다."""

    __tablename__ = "match_results"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("matches.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    result: Mapped[str] = mapped_column(String(10), nullable=False)
    # 아래 3개는 리플레이 파싱으로만 채워진다 (수동 등록 경기는 항상 NULL).
    map_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    game_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    match: Mapped[Match] = relationship(back_populates="result_row")
