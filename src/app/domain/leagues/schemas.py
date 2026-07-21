from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LeagueStatus = Literal["setup", "active", "completed"]
LeagueMode = Literal["team", "individual"]
LeagueMatchSide = Literal["a", "b"]


class LeagueRosterMemberOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    member_id: str = Field(alias="memberId")
    nickname: str
    battletag: str
    avatar: str | None
    position: int


class LeagueTeamOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    label: str
    roster: list[LeagueRosterMemberOut]


class LeagueMatchTeamRefOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    label: str


class LeagueMatchSubstitutionOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    team_id: int = Field(alias="teamId")
    roster_position: int = Field(alias="rosterPosition")
    substitute_member_id: str = Field(alias="substituteMemberId")
    substitute_nickname: str = Field(alias="substituteNickname")
    note: str


class LeagueMatchOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    round: int
    slot_in_round: int = Field(alias="slotInRound")
    team_a: LeagueMatchTeamRefOut | None = Field(alias="teamA")
    team_b: LeagueMatchTeamRefOut | None = Field(alias="teamB")
    is_dead: bool = Field(alias="isDead")
    scheduled_at: datetime | None = Field(alias="scheduledAt")
    sets_won_a: int | None = Field(alias="setsWonA")
    sets_won_b: int | None = Field(alias="setsWonB")
    winner_team_id: int | None = Field(alias="winnerTeamId")
    substitutions: list[LeagueMatchSubstitutionOut]


class LeagueOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str
    mode: LeagueMode
    best_of: int = Field(alias="bestOf")
    status: LeagueStatus
    draw_size: int | None = Field(alias="drawSize")
    planned_teams: int | None = Field(alias="plannedTeams")
    teams: list[LeagueTeamOut]
    matches: list[LeagueMatchOut]
    created_at: datetime = Field(alias="createdAt")


class LeagueListItemOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str
    mode: LeagueMode
    status: LeagueStatus
    team_count: int = Field(alias="teamCount")


class LeagueListOut(BaseModel):
    items: list[LeagueListItemOut]


class LeagueCreateIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=100)
    # 생성 시 확정, 이후 변경 불가(팀 로스터/대타 제약이 여기 달려있어 중간에 바꾸면
    # 이미 만들어진 팀 구성과 모순될 수 있다) — LeagueUpdateIn에는 없음.
    mode: LeagueMode = Field(default="team")
    best_of: int = Field(default=3, alias="bestOf", ge=1, le=99)


class LeagueUpdateIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = Field(default=None, min_length=1, max_length=100)
    best_of: int | None = Field(default=None, alias="bestOf", ge=1, le=99)


class LeagueTeamRosterIn(BaseModel):
    """1~4명(요청: "팀구성은 1~4명 가능")까지는 스키마가 받아주지만, 리그가
    개인전(mode="individual")이면 서비스가 정확히 1명이 아닌 요청을 거부한다 —
    개인전/팀전 여부는 리그 단위 설정이라 스키마만으로는 검증할 수 없다."""

    model_config = ConfigDict(populate_by_name=True)

    member_ids: list[str] = Field(alias="memberIds", min_length=1, max_length=4)

    @model_validator(mode="after")
    def _no_dup(self) -> "LeagueTeamRosterIn":
        if len(set(self.member_ids)) != len(self.member_ids):
            raise ValueError("같은 회원을 두 번 넣을 수 없습니다.")
        return self


class LeagueBracketGenerateIn(BaseModel):
    """대진표를 몇 팀(개인리그면 몇 명)짜리로 잡을지 — 실제 지금 만들어진 팀 수
    (len(teams))와 달라도 된다(요청: "대진표는 팀이 있건 없건 생성 가능하게, 팀수 미리
    설정 가능"). 이미 있는 팀보다 작게는 잡을 수 없다. 상한은 없다(요청: "팀수 무제한
    개인전 선수 무제한 대진표 슬롯 무제한")."""

    model_config = ConfigDict(populate_by_name=True)

    team_count: int = Field(alias="teamCount", ge=2)


class LeagueMatchSlotIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    side: LeagueMatchSide
    team_id: int | None = Field(alias="teamId")


class LeagueMatchScheduleIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    scheduled_at: datetime | None = Field(alias="scheduledAt")


class LeagueMatchSubstituteIn(BaseModel):
    """개인전 리그에서는 서비스가 이 목록을 비어있지 않으면 거부한다(요청: "개인리그면
    ... 대타 지정 불가") — 로스터가 1명뿐이라 대타 개념 자체가 성립하지 않는다."""

    model_config = ConfigDict(populate_by_name=True)

    team_id: int = Field(alias="teamId")
    roster_position: int = Field(alias="rosterPosition", ge=0, le=3)
    substitute_member_id: str = Field(alias="substituteMemberId")
    note: str = Field(default="", max_length=200)


class LeagueMatchResultIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    sets_won_a: int = Field(alias="setsWonA", ge=0)
    sets_won_b: int = Field(alias="setsWonB", ge=0)
    substitutes: list[LeagueMatchSubstituteIn] = Field(default_factory=list)
