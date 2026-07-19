from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import AuditMixin, TimestampMixin
from app.db.types import BigIntPk
from app.domain.members.models import Member


class MatchRequest(AuditMixin, TimestampMixin, Base):
    """"대결 요청" — 너 나와! 화면 최상단 코너에 쌓이는 공개 요청글이다. 특정 상대를 지목하는
    도전장(challenges)과 달리, 아무나 "이런 대결 원해요" 한 줄을 남기면 다른 회원들이 추천
    (엄지척)을 누를 수 있고, 누군가 "들어주기"로 실제 도전장을 보내면 그 요청은 사라진다
    (fulfilled_at 소프트삭제). 정렬은 추천 많은 순 → 먼저 등록된 순."""

    __tablename__ = "match_requests"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 누군가 "들어주기"로 도전장을 보내(또는 작성자가 직접 내려서) 목록에서 사라진 시각.
    # NULL이면 살아있는 요청. 목록 조회는 이 값이 NULL인 것만 내려준다.
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    recommends: Mapped[list["MatchRequestRecommend"]] = relationship(
        back_populates="request", cascade="all, delete-orphan", lazy="selectin",
    )
    # 인스타 태그처럼 본문에 @닉네임으로 지목된 회원들(최소 2명) — 이 사람들만 "들어주기"로
    # 요청을 받아 도전장을 보낼 수 있다(요청). 본문 텍스트와 별개로 실제 대상 회원을 구조적으로
    # 저장해 권한 판정/렌더 하이라이트에 쓴다.
    targets: Mapped[list["MatchRequestTarget"]] = relationship(
        back_populates="request", cascade="all, delete-orphan", lazy="selectin",
    )
    creator: Mapped["Member | None"] = relationship(
        "Member", foreign_keys="MatchRequest.created_by", viewonly=True, lazy="selectin",
    )


class MatchRequestTarget(Base):
    """대결 요청에 언급(지목)된 회원 한 명 — (요청, 회원) 조합은 유일하다. @태그 기능은 폐지돼
    이제 이 목록은 "언급된 사람" 표시 + 알림 대상 용도로만 쓰인다(권한 판정 등 다른 기능과는
    연결하지 않는다). 요청이 등록되면 언급된 각 회원에게 알림이 되며, read_at이 NULL이면
    아직 안 읽은 알림(앱 열 때 인박스 팝업으로 보여준다). 한 번 읽으면 read_at을 채운다."""

    __tablename__ = "match_request_targets"
    __table_args__ = (
        UniqueConstraint("request_id", "member_pk", name="uq_match_request_targets_request_member"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    request_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("match_requests.id", ondelete="CASCADE"), nullable=False
    )
    member_pk: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("members.pk", ondelete="CASCADE"), nullable=False
    )
    # 언급된 회원이 이 요청 알림을 읽은 시각 — NULL이면 안 읽음(인박스에 뜬다).
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    request: Mapped[MatchRequest] = relationship(back_populates="targets")
    member: Mapped[Member] = relationship(foreign_keys=[member_pk], lazy="selectin")


class MatchRequestRecommend(AuditMixin, TimestampMixin, Base):
    """대결 요청 하나에 대한 추천(엄지척) 한 건 — (요청, 회원) 조합은 유일해서 한 회원이
    같은 요청을 두 번 추천할 수 없다(다시 누르면 취소된다)."""

    __tablename__ = "match_request_recommends"
    __table_args__ = (
        UniqueConstraint("request_id", "member_pk", name="uq_match_request_recommends_request_member"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    request_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("match_requests.id", ondelete="CASCADE"), nullable=False
    )
    member_pk: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("members.pk", ondelete="CASCADE"), nullable=False
    )

    request: Mapped[MatchRequest] = relationship(back_populates="recommends")
