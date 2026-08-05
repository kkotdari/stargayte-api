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
    bracket_locked: bool = Field(alias="bracketLocked")
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
    # 생성 시 확정, 이후 변경 불가 — 팀 로스터/대타 제약이 여기 달려있어 중간에 바꾸면 이미
    # 만들어진 팀 구성과 모순될 수 있다(리그 설정을 고치는 경로 자체가 없어졌다).
    mode: LeagueMode = Field(default="team")
    best_of: int = Field(default=3, alias="bestOf", ge=1, le=99)


class LeagueTeamCompositionEntry(BaseModel):
    """팀구성 일괄 저장의 한 팀 — id가 있으면 기존 팀(로스터만 갱신), None이면 새 팀.
    roster는 회원 login id를 순서대로(=로스터 포지션). 새 팀이나 아직 안 채운 팀은 빈 배열."""

    model_config = ConfigDict(populate_by_name=True)

    id: int | None = None
    roster: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def _no_dup(self) -> "LeagueTeamCompositionEntry":
        if len(set(self.roster)) != len(self.roster):
            raise ValueError("한 팀에 같은 회원을 두 번 넣을 수 없습니다.")
        return self


class LeagueTeamCompositionIn(BaseModel):
    """리그의 팀/선수 구성을 화면에서 다 고친 뒤 '팀구성 저장'으로 한 번에 반영한다(요청:
    "팀구성 따로 배치 저장"). teams는 원하는 '전체' 구성(순서=라벨 순서)을 담는다 — 서버가
    기존 팀은 로스터만 갱신, 빠진 팀은 삭제, id=None은 새로 만들고, 라벨을 순서대로 다시
    매겨 원자적으로 반영한다. 저장 후 프론트가 리그를 다시 불러와 대진표도 새 팀으로 갱신한다."""

    model_config = ConfigDict(populate_by_name=True)

    teams: list[LeagueTeamCompositionEntry]


class LeagueBracketSlotIn(BaseModel):
    """일괄 저장에서 자리 하나를 가리키는 법 — 뿌리(우승 자리)에서 내려온 길과 그 자리의 쪽.

    id로 가리키지 않는다: 화면이 가지를 치고 지우는 동안 새 칸에는 아직 id가 없기 때문이다
    (요청: 바로바로 저장이 아니라 마지막 저장 버튼에서 한 번에). path는 "a"/"b"를 이어 붙인
    문자열이고 빈 문자열이 결승이다 — "ab"면 결승의 a쪽으로 올라가는 경기의 b쪽 자리다."""

    model_config = ConfigDict(populate_by_name=True)

    path: str = Field(pattern=r"^[ab]*$", max_length=10)
    side: LeagueMatchSide
    team_id: int | None = Field(default=None, alias="teamId")


class LeagueBracketIn(BaseModel):
    """대진표의 모양과 배정을 한 번에 저장한다(요청: 마지막 저장 버튼에서 한 번에).

    paths는 '있는 경기 전부'를 뿌리에서의 길로 적은 목록이다(빈 문자열=결승). 빈 목록이면
    대진표를 통째로 없앤다. 서버는 이 목록대로 판을 다시 맞춘다 — 그대로인 칸은 행을 그대로
    두고(그래야 id가 안 흔들린다), 없어진 칸은 지우고, 새 칸만 만든다.

    가지를 치고 지우는 조작을 하나하나 API로 부르던 방식(bracket/matches/{id}/{side}/branch)을
    대신한다 — 새 칸의 id와 밀린 라운드 번호가 서버에서 와야 해서 매번 왕복해야 했다."""

    model_config = ConfigDict(populate_by_name=True)

    paths: list[str] = Field(default_factory=list)
    assignments: list[LeagueBracketSlotIn] = Field(default_factory=list)


class LeagueMatchResultIn(BaseModel):
    """세트 스코어(요청: 결과는 몇 대 몇 입력) — 둘 다 None이면 결과를 지운다."""

    model_config = ConfigDict(populate_by_name=True)

    sets_won_a: int | None = Field(default=None, alias="setsWonA", ge=0, le=99)
    sets_won_b: int | None = Field(default=None, alias="setsWonB", ge=0, le=99)

    @model_validator(mode="after")
    def _both_or_neither(self) -> "LeagueMatchResultIn":
        if (self.sets_won_a is None) != (self.sets_won_b is None):
            raise ValueError("세트 스코어는 두 값을 함께 넣거나 함께 비워야 합니다.")
        return self


class LeagueMatchScheduleIn(BaseModel):
    """경기 일시(요청) — None이면 정해진 일시를 지운다."""

    model_config = ConfigDict(populate_by_name=True)

    scheduled_at: datetime | None = Field(default=None, alias="scheduledAt")


class LeagueSeedSlotIn(BaseModel):
    """일괄 시드 저장의 한 자리 — 어느 경기(match_id)의 어느 쪽(side)에 어떤 팀(team_id,
    미지정은 None)이 들어갈지."""

    model_config = ConfigDict(populate_by_name=True)

    match_id: int = Field(alias="matchId")
    side: LeagueMatchSide
    team_id: int | None = Field(alias="teamId")


class LeagueBracketSeedIn(BaseModel):
    """대진표 1라운드 시드를 화면에서 다 고친 뒤 저장 버튼으로 '한 번에' 반영한다(요청:
    "대진표 수정 시 그때그때 저장해서 느림 — 화면만 수정하고 저장 버튼 누르면 한 번에
    저장"). assignments는 편집 가능한 1라운드 슬롯 '전체'의 최종 배정 상태를 담는다 —
    서버는 이 자리들을 먼저 모두 비운 뒤 다시 배정해, 두 팀을 맞바꾸는 것 같은 편집도
    자리별 순차 저장에서 생기던 덮어쓰기 문제 없이 원자적으로 반영한다."""

    model_config = ConfigDict(populate_by_name=True)

    assignments: list[LeagueSeedSlotIn]


