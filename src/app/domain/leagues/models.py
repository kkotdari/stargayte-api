from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import AuditMixin, TimestampMixin
from app.db.types import BigIntPk
from app.domain.members.models import Member


class League(AuditMixin, TimestampMixin, Base):
    """공식 리그(토너먼트) 하나 — 팀 구성/대진표/결과를 관리한다. 상태(setup/active/
    completed)는 컬럼으로 저장하지 않고 draw_size/최종전 결과로 매번 계산한다
    (Challenge의 _status_of와 같은 원칙 — 필드가 하나 늘 때마다 동기화를 신경 쓸
    필요가 없다). draw_size는 bracket/generate 전엔 NULL, 이후 참가 팀 수 기준
    다음 2의 거듭제곱(최대 8)으로 확정된다.

    mode(team/individual)는 생성 시 확정되는 실제 설정값이라 status와 달리 컬럼으로
    저장한다 — "리그가 팀전인지 개인전인지"는 결과로부터 계산될 수 있는 값이 아니다.
    개인전(individual)이면 서비스 레이어가 로스터를 1명으로 고정하고 대타 등록을
    막는다(요청: "개인리그면 로스터 1인으로 고정 및 대타 지정 불가")."""

    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="team")
    best_of: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3)
    draw_size: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # 대진표 생성 시 "몇 팀짜리로 잡을지" 미리 정한 값(요청: "대진표는 팀이 있건 없건
    # 생성 가능하게, 팀수 미리 설정 가능") — 실제 참가 팀 수(len(teams))와 별개다. 팀이
    # 아직 이만큼 안 채워졌어도 나머지 자리는 "예약됨(아직 비었지만 나중에 채워질 수
    # 있음)"으로 두고, draw_size(2의 거듭제곱)를 채우기 위한 패딩만 진짜 부전승(is_dead)
    # 처리한다. bracket/generate 전엔 NULL.
    planned_teams: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    teams: Mapped[list["LeagueTeam"]] = relationship(
        back_populates="league", cascade="all, delete-orphan", lazy="selectin",
        order_by="LeagueTeam.label",
    )
    matches: Mapped[list["LeagueMatch"]] = relationship(
        back_populates="league", cascade="all, delete-orphan", lazy="selectin",
        order_by="LeagueMatch.round, LeagueMatch.slot_in_round",
    )

    __table_args__ = (
        CheckConstraint("best_of >= 1", name="ck_leagues_best_of_positive"),
        CheckConstraint("mode IN ('team', 'individual')", name="ck_leagues_mode_valid"),
        # 상한은 팀리그 6/개인리그 24(요청: "개인전은 최대 24명까지") 중 더 넉넉한 쪽으로
        # 여기서는 24까지만 허용하고, 모드별 정확한 상한은 서비스 레이어(_max_teams)에서
        # 검증한다 — CHECK 제약은 mode 컬럼과 조건부로 엮기 번거로워 느슨한 상한만 건다.
        CheckConstraint(
            "planned_teams IS NULL OR (planned_teams >= 2 AND planned_teams <= 24)",
            name="ck_leagues_planned_teams_range",
        ),
    )


class LeagueTeam(AuditMixin, TimestampMixin, Base):
    """리그 안의 팀 하나 — 라벨은 A~F로 고정, 커스텀 이름은 없다(요청: "팀은 A~F 팀으로
    일단 고정(이름 지정 X)"). 서비스가 생성 순서로 라벨을 부여하고, 팀 삭제 시 남은
    팀들을 다시 A부터 빈틈 없이 재정렬한다."""

    __tablename__ = "league_teams"
    __table_args__ = (
        UniqueConstraint("league_id", "label", name="uq_league_teams_league_label"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    league_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(1), nullable=False)

    league: Mapped[League] = relationship(back_populates="teams")
    roster: Mapped[list["LeagueTeamMember"]] = relationship(
        back_populates="team", cascade="all, delete-orphan", lazy="selectin",
        order_by="LeagueTeamMember.position",
    )


class LeagueTeamMember(Base):
    """팀 로스터 한 자리(0~3, 팀당 최대 4명 — 요청: "팀구성은 1~4명 가능"). league_id를
    league_team_id와 별도로 여기 다시 두는 이유는, "같은 리그 안에서 한 회원은 팀 하나에만"
    (요청 6)이라는 규칙을 UniqueConstraint(league_id, member_pk)로 DB가 직접 강제하기
    위해서다 — league_team_id를 거쳐서는 이 유니크를 만들 수 없다."""

    __tablename__ = "league_team_members"
    __table_args__ = (
        UniqueConstraint("league_team_id", "position", name="uq_league_team_members_team_position"),
        UniqueConstraint("league_id", "member_pk", name="uq_league_team_members_league_member"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    league_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False
    )
    league_team_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("league_teams.id", ondelete="CASCADE"), nullable=False
    )
    member_pk: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("members.pk", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    team: Mapped[LeagueTeam] = relationship(back_populates="roster")
    member: Mapped[Member] = relationship(foreign_keys=[member_pk], lazy="selectin")


class LeagueMatch(AuditMixin, TimestampMixin, Base):
    """대진표 자체 — 별도 "슬롯" 테이블 없이 이 테이블 한 줄 한 줄이 곧 대진판의 한 칸이다
    (team_a_id/team_b_id가 그 칸을 채운 두 팀). round 1이 첫 라운드, 숫자가 커질수록
    결승에 가깝다. is_dead는 팀 수가 2의 거듭제곱이 아니어서(요청: "3/5/6팀일 때 자동
    부전승") 구조적으로 영원히 열리지 않는 칸을 표시한다 — team_a_id/team_b_id가 둘 다
    NULL인 것만으로는 "아직 안 정해짐"과 "영구 공백"을 구분할 수 없어서 이 컬럼이
    반드시 필요하다(서비스의 진출 전파 로직 참고)."""

    __tablename__ = "league_matches"
    __table_args__ = (
        UniqueConstraint("league_id", "round", "slot_in_round", name="uq_league_matches_league_round_slot"),
        CheckConstraint("round >= 1", name="ck_league_matches_round_positive"),
        CheckConstraint("slot_in_round >= 0", name="ck_league_matches_slot_nonneg"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    league_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False
    )
    round: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    slot_in_round: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    team_a_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("league_teams.id", ondelete="SET NULL"), nullable=True
    )
    team_b_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("league_teams.id", ondelete="SET NULL"), nullable=True
    )
    is_dead: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 세트 스코어(요청: "세트 스코어 기록 — 예: 2:1") — 둘 다 NULL이면 아직 실제로
    # 치러지지 않은 경기(부전승으로 승자가 정해졌더라도 이 두 값은 NULL로 남는다).
    sets_won_a: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    sets_won_b: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    winner_team_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("league_teams.id", ondelete="SET NULL"), nullable=True
    )
    result_entered_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("members.pk", ondelete="SET NULL"), nullable=True
    )
    result_entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    league: Mapped[League] = relationship(back_populates="matches")
    team_a: Mapped["LeagueTeam | None"] = relationship(foreign_keys=[team_a_id], lazy="selectin")
    team_b: Mapped["LeagueTeam | None"] = relationship(foreign_keys=[team_b_id], lazy="selectin")
    winner_team: Mapped["LeagueTeam | None"] = relationship(foreign_keys=[winner_team_id], lazy="selectin")
    substitutions: Mapped[list["LeagueMatchSubstitution"]] = relationship(
        back_populates="match", cascade="all, delete-orphan", lazy="selectin",
    )


class LeagueMatchSubstitution(Base):
    """경기 하나에서 로스터 한 자리를 1회성으로 대신하는 대타 기록(요청 7: "대타 제도 있음",
    "해당 경기에만 1회성 적용") — 팀의 정규 로스터(league_team_members)는 이 테이블과
    무관하게 그대로 유지된다."""

    __tablename__ = "league_match_substitutions"
    __table_args__ = (
        UniqueConstraint(
            "league_match_id", "team_id", "roster_position",
            name="uq_league_match_substitutions_match_team_position",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    league_match_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("league_matches.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("league_teams.id", ondelete="CASCADE"), nullable=False
    )
    roster_position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    substitute_member_pk: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("members.pk", ondelete="CASCADE"), nullable=False
    )
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")

    match: Mapped[LeagueMatch] = relationship(back_populates="substitutions")
    substitute: Mapped[Member] = relationship(foreign_keys=[substitute_member_pk], lazy="selectin")
