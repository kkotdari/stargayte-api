from datetime import datetime
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

# 활동 아이템이 품는 내용 — 도전장·게임결과 스키마를 그대로 쓴다. 여기서 다시 정의하면
# 같은 것이 두 벌이 되어 한쪽만 고쳐지는 순간 어긋난다(둘 다 activity를 import하지 않아
# 순환이 생기지 않는다).
from app.domain.challenges.schemas import ChallengeOut
from app.domain.game_results.schemas import GameResultOut

COMMENT_MAX_LENGTH = 50

# 댓글을 달 수 있는 활동 요소 종류 — 새 요소가 생기면 여기에만 추가하면 된다.
ActivityTargetType = Literal["gameResult", "challenge", "rankingShift", "leagueMatch"]
# 위 목록을 그대로 집합으로 — 손으로 한 벌 더 적으면 종류를 늘릴 때 한쪽만 고치게 된다.
KNOWN_TARGET_TYPES = frozenset(get_args(ActivityTargetType))

# 이름 통일(요청) 전에 쓰던 값 → 지금 값. 이 값은 activity_comments.target_type에 그대로
#저장되므로 부팅 때 한 번 일괄로 옮기지만(_migrate_feed_target_types), 배포가 어긋난
# 순간의 옛 프론트가 옛 값을 보낼 수 있어 받는 쪽에서도 계속 받아 준다.
LEGACY_FEED_TARGET_TYPES = {"match": "gameResult", "rankshift": "rankingShift"}

# 요청으로 들어오는 값 — 위 이유로 옛 이름까지 허용하고, normalize_target_type으로 새
# 이름 하나로 모아서 저장/조회한다.
ActivityTargetTypeInput = Literal[
    "gameResult", "challenge", "rankingShift", "leagueMatch", "match", "rankshift",
]


def normalize_target_type(value: str) -> str:
    return LEGACY_FEED_TARGET_TYPES.get(value, value)


def stored_target_types(value: str) -> list[str]:
    """이 대상 종류로 저장돼 있을 수 있는 값 전부 — 지금 이름과 그 옛 이름들.

    저장된 값을 새 이름으로 옮기는 부팅 단계가 따로 있지만(main._migrate_activity_target_types),
    그게 아직 안 돈 DB나 한 번 실패한 DB에서는 옛 이름이 그대로 남는다. 조회를 새 이름
    하나로만 걸면 그런 댓글은 통째로 안 보인다 — 실제로 대상별 조회에서 옛 이름으로 달린
    댓글이 빠졌다(지적: 기존 댓글이 연결 안 됨). 읽을 때는 둘 다 받아 준다.
    """
    new_name = normalize_target_type(value)
    return [new_name, *(old for old, cur in LEGACY_FEED_TARGET_TYPES.items() if cur == new_name)]


class ActivityCommentAuthor(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    member_id: str = Field(alias="memberId")
    nickname: str
    avatar: str | None = None


class ActivityCommentMentionOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    member_id: str = Field(alias="memberId")
    nickname: str


class ActivityCommentOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    target_type: ActivityTargetType = Field(alias="targetType")
    target_id: int = Field(alias="targetId")
    text: str
    author: ActivityCommentAuthor
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    can_edit: bool = Field(alias="canEdit")
    mentions: list[ActivityCommentMentionOut]


class ActivityCommentWrite(BaseModel):
    """댓글 작성/수정 공용 페이로드."""

    model_config = ConfigDict(populate_by_name=True)

    text: str = Field(min_length=1, max_length=COMMENT_MAX_LENGTH)
    target_member_ids: list[str] = Field(
        default_factory=list, alias="targetMemberIds", max_length=20
    )


class ActivityCommentCreate(ActivityCommentWrite):
    target_type: ActivityTargetTypeInput = Field(alias="targetType")
    target_id: int = Field(alias="targetId")


class RankingShiftEntry(BaseModel):
    """스냅샷 간 순위 변동 하나 — from=None 은 신규 진입."""

    model_config = ConfigDict(populate_by_name=True)

    member_id: str = Field(alias="memberId")
    nickname: str
    from_rank: int | None = Field(default=None, alias="from")
    to_rank: int = Field(alias="to")
    # 포인트 변동(요청) — 이 필드가 생기기 전에 쌓인 스냅샷에는 없으므로 둘 다 optional로
    # 둔다. 보여주는 쪽에서 둘 다 있을 때만 증감을 표시한다.
    from_points: int | None = Field(default=None, alias="fromPoints")
    to_points: int | None = Field(default=None, alias="toPoints")


class RankingRecomputeResult(BaseModel):
    """손으로 돌린 하루치 집계의 결과 — 새 스냅샷이 남았는지만 알려 준다. 순위표가 그대로면
    아무것도 안 남는 게 정상인데, 그걸 말해 주지 않으면 "안 돌았나?"로 읽힌다."""

    model_config = ConfigDict(populate_by_name=True)

    changed: bool


class RankingShiftSection(BaseModel):
    """하루치 스냅샷 안의 경기유형 한 칸 — 카드가 좌우로 나눠 그리는 단위다(요청)."""

    model_config = ConfigDict(populate_by_name=True)

    match_type: Literal["0101", "0102"] = Field(alias="matchType")
    shifts: list[RankingShiftEntry]


class RankingShiftOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    reason: str
    created_at: datetime = Field(alias="createdAt")
    match_ids: list[int] = Field(alias="matchIds")
    # 순위표(standings)는 다음 날 비교의 재료일 뿐이라 내보내지 않는다 — 화면이 쓰는 건
    # 변동분(shifts)뿐이고, 회원 수만큼 긴 배열을 매번 실어 보낼 이유가 없다.
    sections: list[RankingShiftSection]


class LeagueMatchMemberOut(BaseModel):
    """리그 팀 로스터 한 사람 — 프사는 화면이 회원 목록에서 찾아 붙인다(닉네임만으로 충분)."""

    model_config = ConfigDict(populate_by_name=True)

    member_id: str = Field(alias="memberId")
    nickname: str


class LeagueMatchTeamActivityOut(BaseModel):
    """맞붙는 한 편 — 로스터를 사람 단위로 내려보낸다.

    한 문자열로 이어 붙여 보내던 것을 나눴다(요청: 본문은 로스터 세로로 배치) — 카드가
    사람마다 프사와 닉네임을 한 줄씩 쌓으려면 이름이 낱개로 와야 한다. 로스터가 비어 있는
    팀은 라벨(A·B)만 남는다.
    """

    model_config = ConfigDict(populate_by_name=True)

    label: str
    members: list[LeagueMatchMemberOut] = Field(default_factory=list)


class LeagueMatchActivityOut(BaseModel):
    """활동 목록에 뜨는 리그 경기 하나 — 일정이 적힌 경기만 여기 온다(요청: 리그 매치에
    일정 등록 시 활동에 띄움).

    리그 화면의 LeagueMatchOut을 그대로 쓰지 않는 이유는, 여기서는 대진표의 좌표(round·
    slot)나 대타 명단이 아니라 '언제 누가 붙나, 결과가 나왔나'만 필요해서다. 리그 이름도
    함께 담는다 — 활동 목록에는 대진표라는 맥락이 없다.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: int
    league_id: int = Field(alias="leagueId")
    league_name: str = Field(alias="leagueName")
    # "8강" 같은 라운드 이름 — 결승까지의 거리로 붙인다.
    round_name: str = Field(alias="roundName")
    team_a: LeagueMatchTeamActivityOut | None = Field(default=None, alias="teamA")
    team_b: LeagueMatchTeamActivityOut | None = Field(default=None, alias="teamB")
    scheduled_at: datetime | None = Field(default=None, alias="scheduledAt")
    sets_won_a: int | None = Field(default=None, alias="setsWonA")
    sets_won_b: int | None = Field(default=None, alias="setsWonB")
    # 어느 쪽이 이겼나 — 팀 이름으로 견주면 두 팀 이름이 같을 때(빈 로스터끼리) 어긋난다.
    winner_side: Literal["a", "b"] | None = Field(default=None, alias="winnerSide")
    # NEW는 '일정을 처음 적어 둔 때', UPDATE는 '마지막으로 손댄 때'로 가른다 — 너 나와의
    # createdAt/updatedAt과 같은 규칙이라 화면 쪽 판정을 그대로 쓴다.
    posted_at: datetime = Field(alias="postedAt")
    updated_at: datetime = Field(alias="updatedAt")


class ActivityItemOut(BaseModel):
    """활동 목록의 아이템 하나 — 너 나와·랭크 변동·게임결과를 같은 것으로 취급한다(요청).

    화면에서 한 줄이 곧 하나다. 종류에 따라 채워지는 칸이 다를 뿐, 줄을 세우고 번호를
    붙이고 댓글을 다는 규칙은 셋이 똑같다.

    게임결과만 여럿(gameResults)인 것은 한 자리에서 이어 친 경기가 한 줄이기 때문이다 —
    줄을 펴면 그 안의 카드들이 나온다. 댓글은 그 줄에 속한 것 전부이고, 각 댓글이 자기
    대상(targetType·targetId)을 그대로 들고 있어 카드마다 제 것을 찾아 붙는다.
    """

    model_config = ConfigDict(populate_by_name=True)

    key: str
    kind: Literal["challenge", "rankingShift", "gameResultPost", "leagueMatch"]
    # 아래에서부터 센 번호(가장 오래된 줄이 1).
    no: int
    challenge: ChallengeOut | None = None
    ranking_shift: RankingShiftOut | None = Field(default=None, alias="rankingShift")
    game_results: list[GameResultOut] = Field(default_factory=list, alias="gameResults")
    league_match: LeagueMatchActivityOut | None = Field(default=None, alias="leagueMatch")
    comments: list[ActivityCommentOut] = Field(default_factory=list)


class ActivityFeedOut(BaseModel):
    """활동 목록 한 페이지 — 화면이 부르는 API는 이것 하나뿐이다(요청).

    순서와 번호는 늘 전체를 놓고 세고 페이지는 그 다음에 자른다 — 화면이 쥔 것만 세면
    아직 안 받아온 과거만큼 번호가 통째로 어긋난다. 그래서 total은 페이지가 아니라
    목록 전체의 줄 수다.
    """

    model_config = ConfigDict(populate_by_name=True)

    # 목록 전체의 줄 수(페이지가 아니라).
    total: int
    # 활동 낱개의 수 — 줄이 아니라 '건'이다. 한 자리에서 이어 친 경기 아홉 판은 줄로는
    # 하나지만 건으로는 아홉이라, 필터 바에 적는 건수는 이 값이어야 한다(지적: 묶는 건
    # 보여주는 방식일 뿐이고 그 안의 판도 각각 한 건이다).
    total_activities: int = Field(alias="totalActivities")
    items: list[ActivityItemOut]
    # 다음 페이지를 부를 때 그대로 돌려주는 값 — 이 페이지 마지막 줄의 열쇠다. 없으면 끝.
    next_cursor: str | None = Field(default=None, alias="nextCursor")
