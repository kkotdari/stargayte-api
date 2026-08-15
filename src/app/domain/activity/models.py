# 테이블 이름도 '활동(activity)'으로 맞춘다 — 코드·API·화면에서 feed/post 표현을 다
# 걷어냈으므로 여기만 옛 이름으로 남을 이유가 없다(요청).
#
# 한 번 옮기다 되돌린 적이 있어 이번엔 운영 상태를 그대로 재현해 확인했다. 되돌린 뒤
# 코드는 feed_comments를 가리키는데 운영 DB는 이미 activity_comments로 바뀌어 있었고,
# 그래서 운영에서 활동 목록이 통째로 500이었다(UndefinedTableError: relation
# "feed_comments" does not exist). 즉 되돌린 것이 오히려 코드와 DB를 갈라놓고 있었다.
# 옛 이름으로 남은 DB는 부팅 때 _TABLE_RENAMES가 옮겨 준다(데이터 그대로).
from sqlalchemy import JSON, BigInteger, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import AuditMixin, TimestampMixin
from app.db.types import BigIntPk
from app.domain.members.models import Member


class ActivityComment(AuditMixin, TimestampMixin, Base):
    """활동 요소 하나에 달리는 댓글 — 대상은 (target_type, target_id)로 가리킨다.

    경기("match")든 너 나와!("challenge")든, 앞으로 추가될 어떤 활동 요소든 같은
    테이블 하나로 담는다. 본문 안 @닉네임 언급은 activity_comment_mentions에 구조적으로
    저장해 현재 닉네임으로 렌더한다. 작성자 본인 또는 운영자만 수정·삭제할 수 있다.
    """

    __tablename__ = "activity_comments"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    mentions: Mapped[list["ActivityCommentMention"]] = relationship(
        back_populates="comment", cascade="all, delete-orphan", lazy="selectin",
    )
    creator: Mapped["Member | None"] = relationship(
        "Member", foreign_keys="ActivityComment.created_by", viewonly=True, lazy="selectin",
    )


class ActivityCommentMention(Base):
    """댓글 본문에 언급(@)된 회원 한 명 — (댓글, 회원) 조합은 유일하다."""

    __tablename__ = "activity_comment_mentions"
    __table_args__ = (
        UniqueConstraint("comment_id", "member_pk", name="uq_activity_comment_mentions_comment_member"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    comment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("activity_comments.id", ondelete="CASCADE"), nullable=False
    )
    member_pk: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("members.pk", ondelete="CASCADE"), nullable=False
    )

    comment: Mapped[ActivityComment] = relationship(back_populates="mentions")
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
                 최초 도입)이 여기 해당한다 — 변동 없이 기준선으로만 남아 활동에 안 보인다.
    """

    __tablename__ = "ranking_shifts"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    reason: Mapped[str] = mapped_column(String(10), nullable=False, default="daily")
    match_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    sections: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class ActivityNotice(TimestampMixin, Base):
    """활동에 뜨는 알림 한 줄(요청: 활동 피드에 알림 유형 추가).

    앞으로 어떤 알림이든 담을 수 있게 종류(kind)와 내용(payload)만 갖는 빈 그릇으로
    둔다(요청: 앞으로 추가될 수도) — 종류가 늘 때마다 테이블을 새로 파면 활동 목록을
    섞는 자리도 그만큼 늘어난다. (처음 담았던 칭호 변경(kind="epithet")은 칭호 개념
    폐지와 함께 사라졌다.)

    - kind:    알림 종류. 화면이 이 값으로 무엇을 그릴지 고른다.
    - payload: 그 종류가 알아서 쓰는 값. 닉네임은 안 담는다 — 이름은 바뀌므로 볼 때
               지금 회원 정보로 푼다.
    """

    __tablename__ = "activity_notices"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
