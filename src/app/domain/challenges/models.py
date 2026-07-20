from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
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
    # 도전장이 "폐기(휴지통)"로 넘어간 시각 — NULL이면 폐기 안 됨. 폐기 사유는 여러 가지다:
    # 상대의 명시적 거절, 응답 마감(무응답 거절), 미실시(not_held) 결과 입력. 상태(_status_of)의
    # 유일한 폐기 판정 근거이자, 휴지통 7일 자동 비움(deleted_at 소프트삭제)의 기준 시각이다.
    # (예전의 취소/연기 기능은 제거됐다 — 취소는 폐기로 통합됐고 연기는 없앴다.)
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 소프트 삭제 — 폐기(discarded_at)된 지 7일이 지나면 목록 조회 시 배치가 이 값을 찍어
    # 이후로는 어떤 조회에도 안 나온다(DB에서는 남겨둔다). NULL이면 살아있음.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 재대결(설욕전) 체인 — 완료된 대결에서 패배한 쪽이 같은 대진으로 다시 신청하면, 원래
    # 행은 그대로 두고 새 행을 만들어 여기에 원래 행의 id를 남긴다. service.py가 이 컬럼을
    # 따라 올라가며 이력(history)을 만든다. (예전엔 거절/취소 뒤 '재신청(reapply)'도 이
    # 체인을 썼지만, 재신청 기능은 제거됐고 이제 체인은 재대결(revenge) 하나뿐이라 chain_kind
    # 컬럼도 없앴다 — reapplied_from_id가 있으면 곧 재대결이다.)
    reapplied_from_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("challenges.id", ondelete="SET NULL"), nullable=True
    )
    # "대결 요청 코너"의 요청을 누군가 "들어주기"로 받아 만든 도전장이면 True — 카드에 "요청대결"
    # 배지를 붙이는 데 쓴다(요청). 일반 도전장은 False.
    from_match_request: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 확정된 대결의 승부 결과 — 이긴 쪽(side)만 기록한다. 예정 일시가 지난 뒤 참가자
    # 누구든 먼저 입력하는 쪽이 그대로 인정된다(요청: "먼저 입력하는 쪽 인정"). 아무도
    # 입력하지 않으면 계속 NULL로 남는다(요청: "그냥 결과 미정으로 계속 남음").
    result_winner_side: Mapped[str | None] = mapped_column(String(10), nullable=True)
    result_entered_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("members.pk", ondelete="SET NULL"), nullable=True
    )
    result_entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    participants: Mapped[list["ChallengeParticipant"]] = relationship(
        back_populates="challenge", cascade="all, delete-orphan", lazy="selectin",
    )
    creator: Mapped["Member | None"] = relationship(
        "Member", foreign_keys="Challenge.created_by", viewonly=True, lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint(
            "result_winner_side IN ('creator','target','draw','not_held')",
            name="ck_challenges_result_winner_side",
        ),
    )


class ChallengeParticipant(Base):
    """도전장 하나에 딸린 참가자 한 명 — match_participants(team1/team2)와 같은 원칙으로,
    "요청자 쪽"(side='creator': 도전자 본인 + 같은 편 팀원)과 "지목된 쪽"(side='target')을
    한 테이블에서 side로만 구분한다. response/responded_at/notified는 side='target' 행에서만
    의미가 있다 — creator 쪽은 개별 수락/거절 없이(도전자가 자기 팀을 구성해 보내는
    것이므로) response가 항상 기본값('pending')에 머문다."""

    __tablename__ = "challenge_participants"
    __table_args__ = (
        UniqueConstraint("challenge_id", "member_pk", name="uq_challenge_participants_challenge_member"),
        CheckConstraint("side IN ('creator','target')", name="ck_challenge_participants_side"),
        CheckConstraint(
            "response IN ('pending','accepted','rejected','discarded')",
            name="ck_challenge_participants_response",
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
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 지목된 사람이 다음 접속 때 팝업으로 한 번 본 뒤로는 다시 안 뜨게 하는 플래그 —
    # 목록/응답 상태 자체와는 별개다(팝업을 이미 봤어도 목록에서는 계속 pending으로 보인다).
    notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 위 notified의 "결과 입력" 팝업 버전 — 예정 일시가 지났는데 결과가 안 들어온 확정
    # 대결을 다음 접속 때 팝업으로 한 번 보여준 뒤 다시 안 뜨게 한다(요청: "결과 입력
    # 팝업 확인 여부는 디비에 관리" — 처음엔 프론트 localStorage로 관리했다가 기기/브라우저를
    # 바꾸면 또 뜨는 문제가 있어 서버로 옮겼다). notified와 달리 결과 입력은 양쪽 참가자
    # 전원이 대상이라 side와 무관하게 모든 행에서 의미가 있다.
    result_notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    challenge: Mapped[Challenge] = relationship(back_populates="participants")
    member: Mapped[Member] = relationship(foreign_keys=[member_pk], lazy="selectin")
