from datetime import date, datetime, time

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import AuditMixin, TimestampMixin
from app.db.types import BigIntPk
from app.domain.members.models import Member


class Schedule(AuditMixin, TimestampMixin, Base):
    """모임 일정 하나 — 누가 언제 무엇을 하자고 올린 글이다(요청: "일정 등록").

    너 나와!와 나란히 서지만 성격이 다르다. 너 나와는 지목한 상대가 있고 그 사람이 답해야
    성사되는 '1:1(또는 팀) 약속'인 반면, 일정은 누구를 지목하지도 않고 답이 없어도 그날
    열린다 — 참가표시는 성사 조건이 아니라 "나 갈게"라는 손들기다. 그래서 상태 개념도,
    폐기·만료도 없다: 올린 사람이 지우기 전까지 그대로 남는다.

    담는 것은 등록 모달에 있는 것 그대로다(요청: 제목*·일시*·내용·링크·파일첨부·참가표시·
    댓글). 댓글만 제 테이블이 없다 — 활동 요소 전부가 activity_comments 하나를 나눠 쓴다.
    """

    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    # 제목 — 유일한 필수 텍스트다(요청: "제목*").
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # 날짜는 필수, 시각은 선택(요청: "일정 일시(시간은 선택)도 필수값"). 한국시간 벽시계값
    # (naive)으로, 사용자가 고른 그대로 저장한다 — 너 나와의 scheduled_date와 같은 규칙이다.
    # 시각을 안 정하면 "시간 미정"이고, 목록에서는 그날 하루의 일로만 읽힌다.
    scheduled_date: Mapped[date] = mapped_column(Date, nullable=False)
    scheduled_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    # 본문(선택) — 여러 줄을 그대로 담는다. 빈 문자열이면 안 적은 것.
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 링크(선택) — 지도·공지·신청 폼처럼 밖으로 나가는 주소 한 줄. 빈 문자열이면 없음.
    link_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 첨부파일 — [{"name", "url", "path", "size"}, ...]. 표를 따로 두지 않고 JSON 한 칸에
    # 담는다: 파일로 검색하거나 정렬할 일이 없고, 일정을 지울 때 함께 지우면 그만이라
    # 조인할 이유가 없다. path는 저장소에서 실제로 지울 때 쓰는 열쇠다.
    files: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    attendees: Mapped[list["ScheduleAttendee"]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan", lazy="selectin",
    )
    creator: Mapped["Member | None"] = relationship(
        "Member", foreign_keys="Schedule.created_by", viewonly=True, lazy="selectin",
    )


class ScheduleAttendee(Base):
    """일정 하나에 대한 한 사람의 참가표시 — 행이 없으면 아직 답 안 함이다.

    '무응답'을 값으로 두지 않는 이유는, 그러면 회원이 늘 때마다 모든 일정에 빈 행을 깔아야
    하기 때문이다. 손을 든 사람만 행이 생기고, 마음을 바꾸면 그 행의 response가 바뀐다
    (다시 안 감으로 되돌리면 행이 사라진다).
    """

    __tablename__ = "schedule_attendees"
    __table_args__ = (
        UniqueConstraint("schedule_id", "member_pk", name="uq_schedule_attendees_schedule_member"),
        CheckConstraint("response IN ('going','notGoing')", name="ck_schedule_attendees_response"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    schedule_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False
    )
    member_pk: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("members.pk", ondelete="CASCADE"), nullable=False
    )
    response: Mapped[str] = mapped_column(String(10), nullable=False, default="going")
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    schedule: Mapped[Schedule] = relationship(back_populates="attendees")
    member: Mapped[Member] = relationship(foreign_keys=[member_pk], lazy="selectin")
