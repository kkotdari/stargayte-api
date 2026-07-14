from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import AuditMixin, TimestampMixin
from app.db.types import BigIntPk
from app.domain.members.models import Member


class Challenge(AuditMixin, TimestampMixin, Base):
    """"너 나와!" 도전장 — 실제 경기결과 시스템과는 독립된 게시판이다. 지목된 회원
    전원이 각자 수락해야 확정(confirmed)되고, 한 명이라도 거부하면 그 즉시 거부로
    끝난다(상태는 저장하지 않고 participants의 response를 매번 계산한다 — 필드가
    하나 늘 때마다 동기화를 신경 쓸 필요가 없다)."""

    __tablename__ = "challenges"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    # 0101=1:1, 0102=팀전 — 폼에서 직접 고르지 않고 지목한 인원수로 서버가 정한다
    # (1명이면 1:1, 2명 이상이면 팀전).
    match_type: Mapped[str] = mapped_column(String(4), nullable=False, default="0101")
    # 미정이면 NULL.
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 확정된 뒤 실제로 경기를 치르고 리플레이를 등록하면, 그 결과(matches.id)를 여기 연결한다.
    # 도전장 게시판 자체는 독립적이지만, 실제로 열린 경기와의 연결 고리만 얇게 남긴다.
    result_match_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("matches.id", ondelete="SET NULL"), nullable=True
    )
    # 요청자(도전자)가 확정 전에 스스로 취소한 시각 — NULL이면 취소 안 됨. 확정된 뒤에는
    # 취소할 수 없다(서비스 레이어에서 막는다).
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    participants: Mapped[list["ChallengeParticipant"]] = relationship(
        back_populates="challenge", cascade="all, delete-orphan", lazy="selectin",
    )
    creator: Mapped["Member | None"] = relationship(
        "Member", foreign_keys="Challenge.created_by", viewonly=True, lazy="selectin",
    )


class ChallengeParticipant(Base):
    """도전장 하나에 딸린 참가자 한 명 — match_participants(team1/team2)와 같은 원칙으로,
    "요청자 쪽"(side='creator': 도전자 본인 + 같은 편 팀원)과 "지목된 쪽"(side='target')을
    한 테이블에서 side로만 구분한다. response/response_message/responded_at/notified는
    side='target' 행에서만 의미가 있다 — creator 쪽은 개별 수락/거절 없이(도전자가 자기
    팀을 구성해 보내는 것이므로) response가 항상 기본값('pending')에 머문다."""

    __tablename__ = "challenge_participants"
    __table_args__ = (
        UniqueConstraint("challenge_id", "member_pk", name="uq_challenge_participants_challenge_member"),
        CheckConstraint("side IN ('creator','target')", name="ck_challenge_participants_side"),
        CheckConstraint(
            "response IN ('pending','accepted','rejected')", name="ck_challenge_participants_response"
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    challenge_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("challenges.id", ondelete="CASCADE"), nullable=False
    )
    member_pk: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("members.pk", ondelete="CASCADE"), nullable=False
    )
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    response: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    # 응답(수락/거절)에 남기는 한마디 — 거절 전용이었다가 수락에도 필수 입력을 받게
    # 되면서 이름을 일반화했다. 전체 공개(누구나 조회 가능).
    response_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 지목된 사람이 다음 접속 때 팝업으로 한 번 본 뒤로는 다시 안 뜨게 하는 플래그 —
    # 목록/응답 상태 자체와는 별개다(팝업을 이미 봤어도 목록에서는 계속 pending으로 보인다).
    notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    challenge: Mapped[Challenge] = relationship(back_populates="participants")
    member: Mapped[Member] = relationship(foreign_keys=[member_pk], lazy="selectin")
