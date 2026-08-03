from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Race = Literal["테란", "프로토스", "저그", "랜덤"]
GameOutcome = Literal["team1", "team2", "draw", "not_held"]
# 경기유형 코드: 0101=1:1, 0102=팀전
GameType = Literal["0101", "0102"]

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


# 유닛·스킬 원장이 담을 수 있는 크기 — 스타의 유닛과 기술을 다 합쳐도 여든을 넘지 않는다.
_MAX_TALLY_KEYS = 80
_MAX_TALLY_KEY_LEN = 40


class BuildMix(BaseModel):
    """그 경기에서 무엇을 짓고 무엇을 뽑았나의 구성(요청) — 값은 전부 커맨드 수이고, 보는
    쪽이 비율로 읽는다. 갈래 이름은 프론트 replayBuildMix.ts와 짝이다.

    음수는 있을 수 없고, 터무니없이 큰 값도 받지 않는다 — 이 값은 화면의 도넛 비율로만
    쓰이므로 이상한 값이 들어와도 티가 잘 안 난다. 들어오는 자리에서 막는 편이 낫다."""

    model_config = ConfigDict(populate_by_name=True)

    b_prod: int = Field(default=0, ge=0, le=100000, alias="bProd")
    b_def: int = Field(default=0, ge=0, le=100000, alias="bDef")
    u_basic: int = Field(default=0, ge=0, le=100000, alias="uBasic")
    u_adv: int = Field(default=0, ge=0, le=100000, alias="uAdv")
    u_caster: int = Field(default=0, ge=0, le=100000, alias="uCaster")
    u_ground: int = Field(default=0, ge=0, le=100000, alias="uGround")
    u_air: int = Field(default=0, ge=0, le=100000, alias="uAir")
    worker5: int = Field(default=0, ge=0, le=100000)
    # 공/방/실드 업그레이드 단계(0~3) — 종족 이름을 지우고 지상/공중 × 공/방 넷과 실드로만
    # 담는다(요청: 종족 무관). 합계로 쌓이므로 상한은 경기 수만큼 커진다.
    up_gw: int = Field(default=0, ge=0, le=100000, alias="upGw")
    up_ga: int = Field(default=0, ge=0, le=100000, alias="upGa")
    up_aw: int = Field(default=0, ge=0, le=100000, alias="upAw")
    up_aa: int = Field(default=0, ge=0, le=100000, alias="upAa")
    up_sh: int = Field(default=0, ge=0, le=100000, alias="upSh")
    # 건물·유닛·스킬 원장(요청: 통계에 Top5 칸) — 이름은 screp 영문 키 그대로 두고 한국어
    # 표기는 화면이 붙인다. 표기를 고치면 이미 등록된 경기도 다음 조회부터 새 표기로
    # 읽히게 하기 위해서다.
    #
    # 자유 형식 사전이라 크기와 값을 여기서 막는다 — 갈래가 정해진 위 항목들과 달리 무엇이든
    # 들어올 수 있는 자리이고, 이 값은 화면의 목록으로만 쓰여 이상한 게 들어와도 티가 안 난다.
    buildings: dict[str, int] = Field(default_factory=dict)
    units: dict[str, int] = Field(default_factory=dict)
    skills: dict[str, int] = Field(default_factory=dict)

    @field_validator("buildings", "units", "skills")
    @classmethod
    def _sane_tally(cls, v: dict[str, int]) -> dict[str, int]:
        if len(v) > _MAX_TALLY_KEYS:
            raise ValueError(f"항목이 너무 많습니다(최대 {_MAX_TALLY_KEYS}개)")
        for name, n in v.items():
            if not name or len(name) > _MAX_TALLY_KEY_LEN:
                raise ValueError("항목 이름이 비었거나 너무 깁니다")
            if not isinstance(n, int) or n < 0 or n > 100000:
                raise ValueError("항목 값이 범위를 벗어났습니다")
        return v


class GameResultSlot(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    member_id: str = Field(alias="memberId")
    # "랜덤"은 회원 프로필의 주종족 개념일 뿐, 실제 경기결과에는 절대 저장하지 않는다
    # (GameResultWrite._normalize 참고). 과거 데이터에는 남아있을 수 있어 읽기 위해 타입 자체는
    # 그대로 두되, 새로 쓰는 값만 검증으로 막는다.
    race: Race
    # 실제 게임에서 쓰인 플레이어 이름 — 리플레이 파싱 원본이거나, 수기등록에서 고른 이름.
    # 보내지 않으면(수기등록 화면이 아직 선택 UI로 바뀌지 않은 경우 등) 서버가 회원의
    # 최근 등록 게임 아이디(placeholder는 예약값)로 채운다 — models.py의
    # GameResultParticipant.player_name, service.py의 GameResultService._player_name 참고. 한 번
    # 저장되면 영구 보존되고 이후 어떤 요청으로도 지우거나 바꿀 수 없다.
    player_name: str | None = Field(default=None, alias="playerName")
    # 아래 4개는 리플레이 파싱으로 자동 등록된 참가자만 값이 있다 (수동 등록은 항상 None).
    apm: int | None = None
    eapm: int | None = None
    cmd_count: int | None = Field(default=None, alias="cmdCount")
    effective_cmd_count: int | None = Field(default=None, alias="effectiveCmdCount")
    # 리플레이 커맨드 스트림에서 센 '생산' 지표(유닛 훈련+건물 건설+변태 커맨드 수).
    build_count: int | None = Field(default=None, alias="buildCount")
    # 그 '생산'의 구성(models.build_mix 주석 참고) — 프론트가 세서 그대로 보낸다.
    build_mix: BuildMix | None = Field(default=None, alias="buildMix")


class GameResultReplayMergeSlot(BaseModel):
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
    build_mix: BuildMix | None = Field(default=None, alias="buildMix")


class ReplayMapData(BaseModel):
    """리플레이에서 뽑은 맵의 지형 격자(models.ReplayMap 주석 참고).

    등록/머지 payload에 실려 오고, 이미 같은 hash가 저장돼 있으면 그냥 버린다(같은 맵을
    두 번 저장하지 않는다). 크기 상한을 두는 이유는 이 값이 통째로 DB에 들어가기 때문이다 —
    브루드워 맵은 최대 256×256이고, 팔레트는 tiles의 한 바이트가 첨자라 256을 넘을 수 없다.
    """

    model_config = ConfigDict(populate_by_name=True)

    hash: str = Field(min_length=8, max_length=64, pattern=r"^[0-9a-f]+$")
    name: str | None = Field(default=None, max_length=150)
    width: int = Field(ge=1, le=256)
    height: int = Field(ge=1, le=256)
    palette: list[int] = Field(min_length=1, max_length=256)
    tiles: str = Field(min_length=1)
    # 자원 지대([타일x, 타일y, 가스여부]) — 앞마당·멀티 자리를 그리는 데 쓴다(요청). 옛
    # 리플레이(이 필드 없이 저장된 맵)와는 호환을 위해 기본 빈 목록.
    resources: list[list[float]] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def _check_size(self) -> "ReplayMapData":
        # base64는 3바이트를 4글자로 옮긴다 — 격자 크기와 안 맞으면 잘렸거나 다른 맵이다.
        expected = ((self.width * self.height + 2) // 3) * 4
        if len(self.tiles) != expected:
            raise ValueError("맵 격자 길이가 맵 크기와 맞지 않습니다.")
        return self


class ReplayMapOut(BaseModel):
    """미니맵을 그리는 쪽으로 내려보내는 맵 격자 — 들어온 것과 같은 형태다."""

    model_config = ConfigDict(populate_by_name=True)

    hash: str
    name: str | None
    width: int
    height: int
    palette: list[int]
    tiles: str
    resources: list[list[float]] = Field(default_factory=list)
    # 사람이 올려 둔 실제 미니맵 그림(data URL) — 있으면 격자 대신 이걸 그린다(요청).
    image: str | None = None


class ReplayMapList(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    maps: list[ReplayMapOut]


# 미니맵 그림 한 장의 상한 — data URL 문자열 길이다(base64라 실제 바이트의 약 4/3). 실제
# 미니맵은 512px 한 장이면 충분하고, 프론트가 올릴 때 그 크기로 줄여 보낸다.
_IMAGE_MAX_CHARS = 900_000


class MinimapImageWrite(BaseModel):
    """미니맵 그림을 새로 올리거나 기존 그림을 고친다. hashes를 함께 주면 그 맵들이 이
    그림을 가리키게 된다(요청: 이름·판본만 다른 맵을 한데 묶기)."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=150)
    # 고칠 때 그림을 그대로 두려면 생략한다.
    image: str | None = Field(default=None, max_length=_IMAGE_MAX_CHARS)
    hashes: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def _check_image(self) -> "MinimapImageWrite":
        if self.image is not None and not self.image.startswith("data:image/"):
            raise ValueError("미니맵 그림은 data:image/... 형식이어야 합니다.")
        return self


class MinimapAssignWrite(BaseModel):
    """맵 여러 개를 한 그림에 붙이거나(imageId) 떼어 낸다(null)."""

    model_config = ConfigDict(populate_by_name=True)

    image_id: int | None = Field(default=None, alias="imageId")
    hashes: list[str] = Field(min_length=1, max_length=64)


class MinimapImageOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str
    image: str


class SummaryRewriteSlot(BaseModel):
    """재분석이 다시 뽑아낸 한 참가자의 값. 짝은 회원 pk가 아니라 리플레이 원본 게임
    아이디(rawName)로 맞춘다 — 회원 연결은 사람이 고쳤을 수 있고, rawName은 그 경기 시점의
    유일한 증거다. 값이 None인 항목은 안 덮어쓴다: 어쩌다 한 지표를 못 읽어도 멀쩡한 기존
    값을 날리지 않게."""

    model_config = ConfigDict(populate_by_name=True)

    raw_name: str = Field(alias="rawName")
    race: Race | None = None
    apm: int | None = None
    eapm: int | None = None
    cmd_count: int | None = Field(default=None, alias="cmdCount")
    effective_cmd_count: int | None = Field(default=None, alias="effectiveCmdCount")
    build_count: int | None = Field(default=None, alias="buildCount")
    build_mix: BuildMix | None = Field(default=None, alias="buildMix")


class SummaryRewrite(BaseModel):
    """이미 등록된 경기의 요약만 다시 계산해 덮어쓴다(요청: 요약 재분석).

    요약은 리플레이에서 규칙으로 뽑아내는 파생 데이터라, 규칙이 좋아지면 옛 경기도 함께
    좋아져야 한다. 그런데 요약을 만드는 파서는 브라우저 쪽에만 있어서(screp-js), 서버가
    스스로 다시 만들 수는 없다 — 화면이 리플레이를 내려받아 다시 분석하고 그 결과만 여기로
    올린다. 경기 내용(팀·승패·참가자)은 건드리지 않는다.
    """

    model_config = ConfigDict(populate_by_name=True)

    summary_data: dict | None = Field(default=None, alias="summaryData")
    # 옛 경기에 미니맵 격자가 없을 수 있어 함께 받는다 — 같은 맵이면 서버가 하나만 남긴다.
    map_data: ReplayMapData | None = Field(default=None, alias="mapData")
    # 요약 말고도 리플레이에서 다시 나오는 값들(요청: 요약뿐 아니라 다른 모든 데이터를 재분석).
    # 화면은 처음부터 이걸 다 보내고 있었는데 여기 자리가 없어 조용히 버려지고 있었다(지적:
    # 경기 재분석을 눌러도 새 컬럼이 안 채워진다) — 파서가 새 값을 내기 시작하면 옛 경기는
    # 재분석으로 따라오는 게 이 기능의 존재 이유다.
    map_name: str | None = Field(default=None, alias="mapName")
    game_started_at: datetime | None = Field(default=None, alias="gameStartedAt")
    duration_seconds: int | None = Field(default=None, alias="durationSeconds")
    slots: list[SummaryRewriteSlot] | None = None


class MapCatalogEntry(BaseModel):
    """제어판 목록의 한 줄 — 격자는 빼고 어떤 맵이 있는지만 본다(격자는 22KB짜리다)."""

    model_config = ConfigDict(populate_by_name=True)

    hash: str
    name: str | None
    width: int
    height: int
    # 이 맵으로 치른 경기 수 — 어느 맵부터 그림을 올릴지 정하는 기준이다.
    matches: int
    image_id: int | None = Field(default=None, serialization_alias="imageId")


class MapCatalog(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    maps: list[MapCatalogEntry]
    images: list[MinimapImageOut]


class GameResultReplayMerge(BaseModel):
    """이미 등록된 경기(game_started_at으로 식별)에 리플레이 내부 정보만 다시 덮어쓰는 머지
    payload(요청: "중복건이라도 머지 방식으로 새 컬럼 덮어쓰기"). 지표(APM/커맨드/생산)·맵·
    플레이시간은 항상 갱신하고, 승패(result)는 리플레이가 승자를 확실히 가린 경우에만(None이면
    유지). 경기번호·등록자·등록일시·메모·참가자 회원연결 같은 건 절대 건드리지 않는다."""

    model_config = ConfigDict(populate_by_name=True)

    game_started_at: datetime = Field(alias="gameStartedAt")
    result: GameOutcome | None = None  # None = 기존 승패 유지(리플레이가 못 가림)
    map_name: str | None = Field(default=None, alias="mapName")
    duration_seconds: int | None = Field(default=None, alias="durationSeconds")
    # 리플레이를 다시 올리면 요약도 다시 계산된 값으로 덮어쓴다(요청: 배치 업로드에서 갱신).
    summary_data: dict | None = Field(default=None, alias="summaryData")
    # 맵 격자 — 예전에 등록해 둔 경기에 미니맵을 채워 넣는 유일한 길이다(옛 경기는 이 값이
    # 아예 없다). 같은 리플레이를 다시 올리면 여기로 들어와 맵 한 벌이 저장되고 경기가 그걸
    # 가리키게 된다.
    map_data: ReplayMapData | None = Field(default=None, alias="mapData")
    players: list[GameResultReplayMergeSlot]


class GameResultReplayMergeResult(BaseModel):
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


class GameResultAuthor(BaseModel):
    id: str
    nickname: str


class GameResultWrite(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    team1: list[GameResultSlot] = Field(min_length=1)
    team2: list[GameResultSlot] = Field(min_length=1)
    result: GameOutcome
    match_type: GameType = Field(default="0101", alias="matchType")
    replay: ReplayUpload | None = None
    # 아래 3개는 리플레이 파싱으로만 채워진다 (수동 등록/수정 시 비워두면 그대로 None).
    map_name: str | None = Field(default=None, alias="mapName")
    game_started_at: datetime | None = Field(default=None, alias="gameStartedAt")
    duration_seconds: int | None = Field(default=None, alias="durationSeconds")
    # 리플레이에서 규칙으로 뽑은 경기 요약 — 문장이 아니라 "무슨 일이 있었나"의 목록이다
    # (models.GameOutcome.summary_data 주석 참고). 사람이 쓴 글이 아니라 파생 데이터다.
    summary_data: dict | None = Field(default=None, alias="summaryData")
    # 이 경기 맵의 지형 격자 — 이미 같은 맵이 저장돼 있으면 버리고 해시만 이어 붙인다.
    # 수기 등록/수정 폼처럼 리플레이를 다시 읽지 않는 경로에서는 None이고, 그때 기존
    # 연결을 지우지 않는다(요약과 같은 규칙).
    map_data: ReplayMapData | None = Field(default=None, alias="mapData")

    @model_validator(mode="after")
    def _normalize(self) -> "GameResultWrite":
        if any(slot.race == "랜덤" for slot in self.team1 + self.team2):
            raise ValueError("경기 참가자의 종족은 실제로 플레이한 종족(테란/프로토스/저그)만 저장할 수 있습니다.")
        return self


class GameResultOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    # 사람이 보고 지목하는 고유번호 — 등록 순서(id)가 아니라 실제 경기 시각 기준이라
    # id와 순서가 다를 수 있다. models.py의 GameResult.match_no 참고.
    match_no: str = Field(alias="matchNo")
    date: str
    team1: list[GameResultSlot]
    team2: list[GameResultSlot]
    result: GameOutcome
    match_type: GameType = Field(alias="matchType")
    replay: ReplayOut | None
    created_by: GameResultAuthor | None = Field(alias="createdBy")
    map_name: str | None = Field(default=None, alias="mapName")
    game_started_at: datetime | None = Field(default=None, alias="gameStartedAt")
    duration_seconds: int | None = Field(default=None, alias="durationSeconds")
    summary_data: dict | None = Field(default=None, alias="summaryData")
    # 이 경기 맵의 지형 격자를 가리키는 해시 — 격자 자체는 따로(GET replay-maps) 받아 온다.
    # 같은 맵을 쓰는 경기가 수십 건이라 목록 응답마다 22KB짜리 격자를 실어 보낼 수 없다.
    map_hash: str | None = Field(default=None, alias="mapHash")
    # 이 경기에 달린 댓글(메모) — 목록 응답에 함께 실어 클라이언트가 펼침 시 바로 렌더하고
    # 검색창에서 댓글 내용으로도 필터할 수 있게 한다(요청). 오래된 순.


class GameResultPage(BaseModel):
    """경기결과 화면 무한스크롤용 커서 페이지."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[GameResultOut]
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
    # 경기당 평균 유효커맨드 — 한때 "분당" 값이었지만 단순 평균으로 되돌렸다(요청).
    avg_ecmd: int | None = Field(default=None, alias="avgEcmd")
    # 경기당 평균 '생산'(유닛 훈련+건물 건설+변태 커맨드 수) — avg_cmd처럼 총합의 단순 평균.
    # 리플레이로 등록된(build_count가 있는) 경기만 반영된다(수동 등록/과거 경기는 NULL).
    avg_build: int | None = Field(default=None, alias="avgBuild")
    # 그 기간 경기들의 생산 구성 합계(요청: 도넛 셋 + 초반 일꾼) — 경기마다 비율을 내서
    # 평균 내지 않고 통째로 더한다. 3분짜리 판과 40분짜리 판의 비율을 같은 무게로 섞으면
    # 짧은 판 한 번이 그 사람의 그림을 흔든다. 구성이 있는 경기가 하나도 없으면 None.
    build_mix: BuildMix | None = Field(default=None, alias="buildMix")
    # 초반 일꾼은 '경기당 몇 기'라야 뜻이 선다 — 위 합계를 경기 수로 나눈 값이다.
    avg_worker5: float | None = Field(default=None, alias="avgWorker5")
    # build_mix에 실제로 더해진 경기 수 — 합계를 경기당 값으로 되돌릴 분모다(평균 건설 수,
    # 공/방 평균 단계). 나눗셈을 화면이 하는 이유는 무엇을 무엇으로 나눌지가 칸마다 달라서다.
    mix_plays: int | None = Field(default=None, alias="mixPlays")


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


class GameResultStatsResponse(BaseModel):
    members: list[MemberStatsEntry]


class RivalryPairOut(BaseModel):
    """유저 상성 한 쌍 — 두 회원의 1:1 상대전적(무승부 별도). a/b는 로그인 아이디."""

    model_config = ConfigDict(populate_by_name=True)

    a: str
    b: str
    a_wins: int = Field(alias="aWins")
    b_wins: int = Field(alias="bWins")
    draws: int = 0


class RivalryResponse(BaseModel):
    pairs: list[RivalryPairOut]


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
