from sqlalchemy import BigInteger, ForeignKey, String, Text, UniqueConstraint
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
