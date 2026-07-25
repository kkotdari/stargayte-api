from sqlalchemy import JSON, BigInteger, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import AuditMixin, TimestampMixin
from app.db.types import BigIntPk
from app.domain.members.models import Member


class FeedComment(AuditMixin, TimestampMixin, Base):
    """피드 요소 하나에 달리는 댓글 — 대상은 (target_type, target_id)로 가리킨다.

    경기("match")든 너 나와!("challenge")든, 앞으로 추가될 어떤 피드 요소든 같은
    테이블 하나로 담는다. 본문 안 @닉네임 언급은 feed_comment_mentions에 구조적으로
    저장해 현재 닉네임으로 렌더한다. 작성자 본인 또는 운영자만 수정·삭제할 수 있다.
    """

    __tablename__ = "feed_comments"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    mentions: Mapped[list["FeedCommentMention"]] = relationship(
        back_populates="comment", cascade="all, delete-orphan", lazy="selectin",
    )
    creator: Mapped["Member | None"] = relationship(
        "Member", foreign_keys="FeedComment.created_by", viewonly=True, lazy="selectin",
    )


class FeedCommentMention(Base):
    """댓글 본문에 언급(@)된 회원 한 명 — (댓글, 회원) 조합은 유일하다."""

    __tablename__ = "feed_comment_mentions"
    __table_args__ = (
        UniqueConstraint("comment_id", "member_pk", name="uq_feed_comment_mentions_comment_member"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    comment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("feed_comments.id", ondelete="CASCADE"), nullable=False
    )
    member_pk: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("members.pk", ondelete="CASCADE"), nullable=False
    )

    comment: Mapped[FeedComment] = relationship(back_populates="mentions")
    member: Mapped[Member] = relationship(foreign_keys=[member_pk], lazy="selectin")


class RankSnapshot(TimestampMixin, Base):
    """경기 등록/삭제 시점의 포인트·순위 스냅샷 — 매번 다시 계산하지 않도록 저장해 둔다.

    한 이벤트(배치 등록/삭제)당 경기유형별로 한 행. 직전 스냅샷과 비교한 변동분(shifts)이
    비어 있지 않은 행만 피드에 노출된다(빈 행은 다음 비교의 기준으로만 쓰인다).
    - standings: [{"memberId", "nickname", "points", "rank"}, ...] (그 시점 전체 순위표)
    - shifts:    [{"memberId", "nickname", "from", "to"}, ...] (from=None 은 신규 진입)
    - match_ids: 이 이벤트를 만든 경기 id들(배치면 여러 개, 전체 삭제면 빈 배열)
    - reason:    "register" | "delete" | "seed"(부팅 시 최초 기준 적재)
    """

    __tablename__ = "rank_snapshots"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    match_type: Mapped[str] = mapped_column(String(4), nullable=False, index=True)  # 0101=개인전, 0102=팀전
    reason: Mapped[str] = mapped_column(String(10), nullable=False, default="register")
    match_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    standings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    shifts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
