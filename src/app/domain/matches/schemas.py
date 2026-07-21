from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Race = Literal["테란", "프로토스", "저그", "랜덤"]
MatchResult = Literal["team1", "team2", "draw", "not_held"]
# 경기유형 코드: 0101=1:1, 0102=팀전
MatchType = Literal["0101", "0102"]

# 실제 회원이 아니라 "컴퓨터"(AI) 참가자를 나타내는 memberId 접두사 — 가끔 컴퓨터를 끼고
# 하는 경기가 있어(팀전 인원을 채우는 등) 실제 회원 없이도 슬롯을 채울 수 있게 한다.
# 프론트에서 매 슬롯마다 고유하게 생성해 보내고, 회원 존재 검증/통계 집계에서는 항상
# 이 접두사인지로 걸러내며 실제로는 DB에 저장하지 않는다(응답 시 position 기반으로 재생성).
COMPUTER_ID_PREFIX = "__computer__"
# 아직 가입하지 않은 실제 사람 — 컴퓨터와 마찬가지로 실제 회원 없이 슬롯을 채우되, 나중에
# 그 사람이 가입하면(또는 인게임 아이디를 알게 되면) 회원과 수동으로 연결할 수 있다는 점만
# 다르다. DB 처리 방식(회원 없음/position 기반 재생성)은 컴퓨터와 동일 — 리플레이가 파싱한
# 실제 이름(player_name)을 그대로 저장하고, replay_aliases.kind 조회로 분류한다.
UNREGISTERED_ID_PREFIX = "__unregistered__"


def is_computer_slot(member_id: str) -> bool:
    return member_id.startswith(COMPUTER_ID_PREFIX)


def is_unregistered_slot(member_id: str) -> bool:
    return member_id.startswith(UNREGISTERED_ID_PREFIX)


def is_placeholder_slot(member_id: str) -> bool:
    """실제 회원이 아닌 슬롯(컴퓨터/비회원) 공통 판별 — 회원 조회/중복 검사 등
    실제 회원 여부만 중요한 곳에서 둘을 같이 걸러낼 때 쓴다."""
    return is_computer_slot(member_id) or is_unregistered_slot(member_id)


class MatchSlot(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    member_id: str = Field(alias="memberId")
    # "랜덤"은 회원 프로필의 주종족 개념일 뿐, 실제 경기결과에는 절대 저장하지 않는다
    # (MatchWrite._normalize 참고). 과거 데이터에는 남아있을 수 있어 읽기 위해 타입 자체는
    # 그대로 두되, 새로 쓰는 값만 검증으로 막는다.
    race: Race
    # 실제 게임에서 쓰인 플레이어 이름 — 리플레이 파싱 원본이거나, 수기등록에서 고른 이름.
    # 보내지 않으면(수기등록 화면이 아직 선택 UI로 바뀌지 않은 경우 등) 서버가 회원의
    # 최근 등록 게임 아이디(placeholder는 예약값)로 채운다 — models.py의
    # MatchParticipant.player_name, service.py의 MatchService._player_name 참고. 한 번
    # 저장되면 영구 보존되고 이후 어떤 요청으로도 지우거나 바꿀 수 없다.
    player_name: str | None = Field(default=None, alias="playerName")
    # 아래 4개는 리플레이 파싱으로 자동 등록된 참가자만 값이 있다 (수동 등록은 항상 None).
    apm: int | None = None
    eapm: int | None = None
    cmd_count: int | None = Field(default=None, alias="cmdCount")
    effective_cmd_count: int | None = Field(default=None, alias="effectiveCmdCount")
    # 리플레이 커맨드 스트림에서 센 '생산' 지표(유닛 훈련+건물 건설+변태 커맨드 수).
    build_count: int | None = Field(default=None, alias="buildCount")


class MatchReplayMergeSlot(BaseModel):
    """리플레이 재파싱으로 갱신할 한 참가자의 값 — player_name(리플레이 원본 게임 아이디)으로
    기존 참가자를 찾아 지표/종족만 덮어쓴다. 회원 연결(누가 뛰었는지)은 건드리지 않는다."""

    model_config = ConfigDict(populate_by_name=True)

    player_name: str = Field(alias="playerName")
    race: Race | None = None
    apm: int | None = None
    eapm: int | None = None
    cmd_count: int | None = Field(default=None, alias="cmdCount")
    effective_cmd_count: int | None = Field(default=None, alias="effectiveCmdCount")
    build_count: int | None = Field(default=None, alias="buildCount")


class MatchReplayMerge(BaseModel):
    """이미 등록된 경기(game_started_at으로 식별)에 리플레이 내부 정보만 다시 덮어쓰는 머지
    payload(요청: "중복건이라도 머지 방식으로 새 컬럼 덮어쓰기"). 지표(APM/커맨드/생산)·맵·
    플레이시간은 항상 갱신하고, 승패(result)는 리플레이가 승자를 확실히 가린 경우에만(None이면
    유지). 경기번호·등록자·등록일시·메모·참가자 회원연결 같은 건 절대 건드리지 않는다."""

    model_config = ConfigDict(populate_by_name=True)

    game_started_at: datetime = Field(alias="gameStartedAt")
    result: MatchResult | None = None  # None = 기존 승패 유지(리플레이가 못 가림)
    map_name: str | None = Field(default=None, alias="mapName")
    duration_seconds: int | None = Field(default=None, alias="durationSeconds")
    players: list[MatchReplayMergeSlot]


class MatchReplayMergeResult(BaseModel):
    """머지 결과 — 게임 시각이 일치하는 경기가 있어 실제로 덮어썼는지(merged)와 그 경기번호."""

    model_config = ConfigDict(populate_by_name=True)

    merged: bool
    match_no: str | None = Field(default=None, alias="matchNo")


class ReplayUpload(BaseModel):
    """리플레이 업로드 payload. url은 data URL(신규 업로드) 또는 기존 서버 URL(변경 없음).
    original_name은 원본 파일명, display_name은 프론트가 만든 알아보기 쉬운 파일명이다."""

    model_config = ConfigDict(populate_by_name=True)

    original_name: str = Field(alias="originalName")
    display_name: str = Field(alias="displayName")
    url: str


class ReplayOut(BaseModel):
    """응답용 리플레이 정보 — url은 항상 서버에 저장된 다운로드 URL."""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    original_name: str = Field(alias="originalName")
    display_name: str = Field(alias="displayName")
    url: str


class MatchAuthor(BaseModel):
    id: str
    nickname: str


class MatchWrite(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    team1: list[MatchSlot] = Field(min_length=1)
    team2: list[MatchSlot] = Field(min_length=1)
    result: MatchResult
    match_type: MatchType = Field(default="0101", alias="matchType")
    note: str = ""
    replay: ReplayUpload | None = None
    # 아래 3개는 리플레이 파싱으로만 채워진다 (수동 등록/수정 시 비워두면 그대로 None).
    map_name: str | None = Field(default=None, alias="mapName")
    game_started_at: datetime | None = Field(default=None, alias="gameStartedAt")
    duration_seconds: int | None = Field(default=None, alias="durationSeconds")

    @model_validator(mode="after")
    def _normalize(self) -> "MatchWrite":
        if any(slot.race == "랜덤" for slot in self.team1 + self.team2):
            raise ValueError("경기 참가자의 종족은 실제로 플레이한 종족(테란/프로토스/저그)만 저장할 수 있습니다.")
        return self


class MatchMemoWrite(BaseModel):
    """전체 회원에게 열려있는 가벼운 메모 — 팀/결과 등 실제 경기 데이터를 바꾸는 정식 수정
    (MatchWrite/update_match, 작성자·운영자만)과 달리 note 한 필드만 갈아치운다."""

    note: str = ""


class MatchOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    # 사람이 보고 지목하는 고유번호 — 등록 순서(id)가 아니라 실제 경기 시각 기준이라
    # id와 순서가 다를 수 있다. models.py의 Match.match_no 참고.
    match_no: str = Field(alias="matchNo")
    date: str
    team1: list[MatchSlot]
    team2: list[MatchSlot]
    result: MatchResult
    match_type: MatchType = Field(alias="matchType")
    note: str
    replay: ReplayOut | None
    created_by: MatchAuthor | None = Field(alias="createdBy")
    map_name: str | None = Field(default=None, alias="mapName")
    game_started_at: datetime | None = Field(default=None, alias="gameStartedAt")
    duration_seconds: int | None = Field(default=None, alias="durationSeconds")


class MatchPage(BaseModel):
    """경기결과 화면 무한스크롤용 커서 페이지."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[MatchOut]
    next_cursor: str | None = Field(alias="nextCursor")
    has_more: bool = Field(alias="hasMore")
    # 같은 필터 조건에 해당하는 전체 건수 — 무한스크롤로 일부만 로드된 상태에서도 화면에
    # 정확한 총 건수를 보여주기 위함. 매 페이지마다 다시 셀 필요는 없어 첫 페이지(커서
    # 없음) 응답에만 채우고, 이후 페이지는 null(프론트가 첫 응답 값을 계속 들고 있는다).
    total: int | None = Field(default=None)


class RaceStatsEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    plays: int
    wins: int
    losses: int
    draws: int
    win_rate: float = Field(alias="winRate")
    avg_apm: int | None = Field(default=None, alias="avgApm")
    avg_eapm: int | None = Field(default=None, alias="avgEapm")
    avg_cmd: int | None = Field(default=None, alias="avgCmd")
    # 총합의 평균이 아니라 "분당" 값(ecmd_sum / 총 경기시간(분)) — 경기 길이가 제각각이라
    # 총합만 평균 내면 불공정하다.
    avg_ecmd: int | None = Field(default=None, alias="avgEcmd")
    # 경기당 평균 '생산'(유닛 훈련+건물 건설+변태 커맨드 수) — avg_cmd처럼 총합의 단순 평균.
    # 리플레이로 등록된(build_count가 있는) 경기만 반영된다(수동 등록/과거 경기는 NULL).
    avg_build: int | None = Field(default=None, alias="avgBuild")


class MemberStatsEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    member_id: str = Field(alias="memberId")
    overall: RaceStatsEntry
    by_race: dict[str, RaceStatsEntry] = Field(alias="byRace")
    most_played_race: str | None = Field(default=None, alias="mostPlayedRace")
    # 랭킹 순서 — 승률만으로는 못 가르는 동률을 승자승(맞대결)/공통상대/전체 승수로 마저
    # 가른 최종 정렬 결과다. 맞대결·공통상대 성적은 "누구와 누구를 비교하느냐"에 따라
    # 달라지는 쌍(pair) 단위 값이라 회원 하나의 숫자로 내려보낼 수가 없어서, 서버가 정렬을
    # 끝내고 그 자리 번호만 실어 보낸다(프론트는 이 값으로만 줄세운다). 이 요청 조건(기간/
    # 유형/종족)에서 한 경기도 안 뛴 회원은 애초에 순위 대상이 아니라 None.
    sort_order: int | None = Field(default=None, alias="sortOrder")
    # 위 모든 기준까지 전부 같아 진짜 완전 동률인 회원들은 이 값이 서로 같다 — 화면이
    # 공동순위(같은 등수)로 묶는 기준. 순위 대상이 아니면 None.
    tie_group: int | None = Field(default=None, alias="tieGroup")
    # 랭킹의 2순위 기준값(승자승 다음) — 붙어본 상대 한 명 한 명에 대해 우세 +1 / 동등 0 /
    # 열세 -1을 합산한 '사람단위 점수'다. 경기 수·점수차는 무시한다. 카드에 이 숫자를
    # 보여줘 화면 순위와 앞뒤가 맞게 한다(예전의 경기 승점(승-패) 자리를 대체). 순위 대상이
    # 아니면 None.
    person_score: int | None = Field(default=None, alias="personScore")
    # 사람단위 점수의 내역 — 몇 명에게 우세/동등/열세인지(인원수). 상세 화면에서 쓴다.
    # 순위 대상이 아니면 None.
    superior_count: int | None = Field(default=None, alias="superiorCount")
    equal_count: int | None = Field(default=None, alias="equalCount")
    inferior_count: int | None = Field(default=None, alias="inferiorCount")
    # 랭킹 총점 — TrueSkill 보수추정 레이팅(μ−3σ, 첫째 자리 반올림). 카드에 이 숫자를 보여주고
    # 이 값으로 순위를 매긴다(음수 가능). 순위 대상 아니면 None.
    rank_score: float | None = Field(default=None, alias="rankScore")
    # TrueSkill 실력 추정치(μ)와 불확실성(σ) — 상세/뱃지 표시용. 순위 대상 아니면 None.
    mu: float | None = Field(default=None)
    sigma: float | None = Field(default=None)
    # 이 경기유형에서 누적된(레이팅에 반영된) 경기 수. 순위 대상 아니면 None.
    rating_games: int | None = Field(default=None, alias="ratingGames")
    # 잠정 — 누적 경기가 기준 미만이라 레이팅이 아직 덜 여문 상태(뱃지로 표시). 순위 대상 아니면 None.
    provisional: bool | None = Field(default=None)


class MatchStatsResponse(BaseModel):
    members: list[MemberStatsEntry]


class RankingResponse(MatchStatsResponse):
    """랭킹 조회 전용 응답 — 구조는 전적통계(MatchStatsResponse)와 같지만(회원별 전적 +
    순위/레이팅), URL 의미(랭킹)에 맞게 별도 이름으로 노출한다(요청: "랭킹 엔드포인트
    분리"). 백엔드 산정 로직은 get_stats를 그대로 공유한다."""


class RatingHistoryResponse(BaseModel):
    """랭킹 상세의 '경기당 레이팅 변화(Δ)' — 이 회원이 뛴 각 경기의 μ 증감(match_no로 키잉).

    레이팅은 시간순 누적이라 경기당 변화는 그 시점 상태에 따라 달라져 클라이언트가 재구성할
    수 없다 — 백엔드가 전체를 재생하며 이 회원 경기마다의 Δμ를 계산해 준다. 함께 현재 레이팅
    요약(μ/σ/보수/누적경기/잠정)도 싣는다."""

    model_config = ConfigDict(populate_by_name=True)

    # matchNo -> 그 경기에서의 μ 변화량(양수=상승). 첫째 자리 반올림.
    deltas: dict[str, float]
    mu: float | None = Field(default=None)
    sigma: float | None = Field(default=None)
    conservative: float | None = Field(default=None)
    games: int = 0
    provisional: bool = False


class MemberStatsMonthEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # "YYYY-MM" — 요청한 순서 그대로 돌려준다.
    month: str
    members: list[MemberStatsEntry]


class MonthlyMatchStatsResponse(BaseModel):
    """랭킹 화면의 월별 순위변동(최근 5개월) 비교와, 목록의 전월 대비 순위 화살표가 함께
    쓴다 — 달마다 따로 요청을 보내는 대신 한 번에 여러 달을 받아 왕복을 줄인다."""

    months: list[MemberStatsMonthEntry]


class TeamRankEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # 그 팀을 이룬 회원들의 로그인 아이디 — 개인 승점이 높은 순으로 이미 정렬돼 있다
    # (화면이 이 순서 그대로 2×2 격자에 왼→오, 위→아래로 채운다).
    member_ids: list[str] = Field(alias="memberIds")
    plays: int
    wins: int
    losses: int
    draws: int
    # 승 +1, 무 0, 패 -1 — 음수가 될 수 있다.
    points: int


class TeamRankingResponse(BaseModel):
    # dateFrom/dateTo를 안 넘기면 전체 경기 집계, 넘기면(랭킹 화면의 월 기준 기본 집계) 그
    # 기간만 대상 — 어느 쪽이든 응답 자체에는 기간 정보를 다시 싣지 않는다(요청한 쪽이 이미
    # 알고 있다).
    teams: list[TeamRankEntry]


class TeamRankMonthEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    month: str
    teams: list[TeamRankEntry]


class MonthlyTeamRankingResponse(BaseModel):
    months: list[TeamRankMonthEntry]


class MainRaceResponse(BaseModel):
    race: str | None


class DuplicateCheckRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    game_started_at: list[str] = Field(alias="gameStartedAt", max_length=50)


class DuplicateCheckResponse(BaseModel):
    existing: list[str]


class EarliestDateResponse(BaseModel):
    date: str | None


# 배틀태그로 못 찾은 리플레이 참가자 이름을 컴퓨터/비회원으로 기억해두는 매핑 —
# members.models.ReplayAlias의 kind 값과 동일해야 한다(models.py의 CHECK 제약과 일치).
ReplayNameKind = Literal["computer", "unregistered"]


class ReplayNameClassificationLookupRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    raw_names: list[str] = Field(alias="rawNames", max_length=100)


class ReplayNameClassificationEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    raw_name: str = Field(alias="rawName")
    kind: ReplayNameKind


class ReplayNameClassificationLookupResponse(BaseModel):
    classifications: list[ReplayNameClassificationEntry]


class ReplayNameClassificationWrite(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    raw_name: str = Field(alias="rawName", min_length=1, max_length=100)
    kind: ReplayNameKind


# 유저 매핑 관리 화면 — 리플레이 원본 이름(rawName) 하나를 "기준"으로, 그게 지금 회원/
# 컴퓨터/비회원 중 무엇으로 연결돼 있는지(또는 아직 연결이 없는지) 보여주고 바꿀 수
# 있게 한다. replay_aliases 테이블(회원 매칭/컴퓨터·비회원 분류를 함께 담는다)을
# rawName 하나 기준으로 보여준다.
ReplayNameMappingKind = Literal["member", "computer", "unregistered", "unresolved"]


class ReplayNameMappingMember(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    nickname: str
    battletag: str
    avatar: str | None = None


class ReplayNameMappingEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    raw_name: str = Field(alias="rawName")
    kind: ReplayNameMappingKind
    member: ReplayNameMappingMember | None = None
    # 이 이름이 마지막으로 등장한 경기 날짜 — 미해결 항목을 최근 순으로 보여주는 데 쓴다.
    # 단건 저장 응답(set)에서는 다시 조회하지 않아 항상 None.
    last_seen: date | None = Field(default=None, alias="lastSeen")
    # 이 게임아이디로 등록된 경기가 하나라도 있는지 — 있으면 휴지통(완전 삭제)이 막힌다
    # (화면에서 경고를 띄우고 삭제 버튼을 못 누르게 한다). 단건 저장 응답에서는 False.
    has_matches: bool = Field(default=False, alias="hasMatches")


class ReplayNameMappingListResponse(BaseModel):
    entries: list[ReplayNameMappingEntry]


class ReplayNameMappingWrite(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    raw_name: str = Field(alias="rawName", min_length=1, max_length=100)
    kind: ReplayNameMappingKind
    # kind가 "member"일 때만 필요 — 대상 회원의 로그인 아이디(members.id).
    member_id: str | None = Field(default=None, alias="memberId")
