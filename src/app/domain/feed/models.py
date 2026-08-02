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


class RankingShift(TimestampMixin, Base):
    """하루치 포인트·순위 스냅샷 — 하루에 한 행이고, 그 안에 경기유형별 칸을 담는다.

    예전에는 유형(개인전/팀전)마다 한 행이었다. 그러면 같은 날 아침에 카드가 두 장 뜨고,
    댓글도 두 갈래로 갈렸다 — 카드를 한 장으로 합치면서(요청) 저장도 하루 한 행으로 모은다.
    앞으로 유형이 늘어도 sections에 칸을 더하면 되므로 스키마를 다시 안 건드린다.

    - sections:  [{"matchType": "0101", "standings": [...], "shifts": [...]}, ...]
                 · standings: [{"memberId", "nickname", "points", "rank"}, ...] (그 시점 순위표)
                 · shifts:    [{"memberId", "nickname", "from", "to", ...}, ...] (from=None 은 신규)
                 유형별 칸은 그날 순위표가 있으면 변동이 없어도 남는다 — 다음 날 비교의
                 기준이 그 칸이기 때문이다.
    - match_ids: 이 스냅샷을 만든 경기 id들(하루치 집계에서는 빈 배열)
    - reason:    "daily" | "seed"
                 seed = 비교 기준선. 비교할 '같은 달' 스냅샷이 없는 날(매달 첫 집계,
                 최초 도입)이 여기 해당한다 — 변동 없이 기준선으로만 남아 피드에 안 보인다.
    """

    __tablename__ = "ranking_shifts"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    reason: Mapped[str] = mapped_column(String(10), nullable=False, default="daily")
    match_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    sections: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
