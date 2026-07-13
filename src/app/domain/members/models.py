from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import AuditMixin, TimestampMixin
from app.db.types import BigIntPk


class Member(AuditMixin, TimestampMixin, Base):
    __tablename__ = "members"

    # 로그인 아이디(id)는 나중에 바뀔 수 있으므로 FK/토큰 등 내부 식별에는 쓰지 않는다.
    # pk가 절대 변하지 않는 진짜 식별자다.
    pk: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nickname: Mapped[str] = mapped_column(String(100), nullable=False)
    battletag: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    insta: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 가입 시 pending으로 시작해 운영자가 승인(active)하기 전까지는 로그인/이용이 막힌다.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    # 한 회원이 여러 역할을 가질 수 있는 구조지만, 실제로는 운영자(0202)/회원(0203) 둘 중
    # 하나만 갖는다.
    roles: Mapped[list["MemberRoleAssignment"]] = relationship(
        back_populates="member", cascade="all, delete-orphan", lazy="selectin"
    )

    # 리플레이(.rep)에 실제로 기록되는 게임 내 표시 이름 — battletag와 다를 수 있어(예전
    # Battle.net 계정명, 부계정 등) 리플레이 일괄 등록의 회원 매칭 전용으로 별도 저장한다.
    # 회원 하나당 등록 개수 제한은 없다. ReplayAlias는 computer/unregistered 분류(member_pk가
    # NULL)도 같은 테이블에 담는 통합 모델이라, 이 FK 기반 relationship은 자연히 member_pk가
    # 채워진(kind='member') 행만 골라온다.
    replay_aliases: Mapped[list["ReplayAlias"]] = relationship(
        back_populates="member", cascade="all, delete-orphan", lazy="selectin",
        order_by="ReplayAlias.created_at",
    )

    @property
    def role_codes(self) -> list[str]:
        return [r.role for r in self.roles]

    def has_any_role(self, *codes: str) -> bool:
        return any(r.role in codes for r in self.roles)

    @property
    def replay_alias_values(self) -> list[str]:
        return [e.raw_name for e in self.replay_aliases]


class MemberRoleAssignment(Base):
    """회원-역할 다대다 관계. 0202=운영자, 0203=회원."""

    __tablename__ = "member_roles"
    __table_args__ = (UniqueConstraint("member_pk", "role", name="uq_member_roles_member_role"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    member_pk: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("members.pk", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    member: Mapped[Member] = relationship(back_populates="roles")


class ReplayAlias(Base):
    """리플레이 원본 이름(raw_name) 하나가 무엇을 가리키는지의 통합 매핑. kind='member'면
    member_pk가 그 이름을 등록한 회원을 가리키고(회원 하나당 등록 개수 제한 없음), kind가
    'computer'/'unregistered'면 실제 회원이 아닌 참가자를 가리키는 전역 분류라 member_pk가
    없다. raw_name은 이 셋 중 항상 정확히 하나만 가리켜야 하므로 테이블 전체에서 유일하다."""

    __tablename__ = "replay_aliases"
    __table_args__ = (
        UniqueConstraint("raw_name", name="uq_replay_aliases_raw_name"),
        CheckConstraint(
            "kind IN ('member', 'computer', 'unregistered')", name="ck_replay_aliases_kind"
        ),
        CheckConstraint(
            "(kind = 'member') = (member_pk IS NOT NULL)", name="ck_replay_aliases_member_pk_matches_kind"
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    raw_name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    member_pk: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("members.pk", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    member: Mapped[Member | None] = relationship(back_populates="replay_aliases")
