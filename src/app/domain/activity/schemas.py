from datetime import datetime
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

COMMENT_MAX_LENGTH = 50

# 댓글을 달 수 있는 활동 요소 종류 — 새 요소가 생기면 여기에만 추가하면 된다.
ActivityTargetType = Literal["gameResult", "challenge", "rankingShift"]
# 위 목록을 그대로 집합으로 — 손으로 한 벌 더 적으면 종류를 늘릴 때 한쪽만 고치게 된다.
KNOWN_TARGET_TYPES = frozenset(get_args(ActivityTargetType))

# 이름 통일(요청) 전에 쓰던 값 → 지금 값. 이 값은 activity_comments.target_type에 그대로
#저장되므로 부팅 때 한 번 일괄로 옮기지만(_migrate_feed_target_types), 배포가 어긋난
# 순간의 옛 프론트가 옛 값을 보낼 수 있어 받는 쪽에서도 계속 받아 준다.
LEGACY_FEED_TARGET_TYPES = {"match": "gameResult", "rankshift": "rankingShift"}

# 요청으로 들어오는 값 — 위 이유로 옛 이름까지 허용하고, normalize_target_type으로 새
# 이름 하나로 모아서 저장/조회한다.
ActivityTargetTypeInput = Literal["gameResult", "challenge", "rankingShift", "match", "rankshift"]


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


class ActivityListRow(BaseModel):
    """활동 목록 한 줄 — 화면에 보이는 줄 하나에 번호 하나(요청: "한 줄 = 1번").

    내용은 안 싣는다. 카드에 필요한 값은 이미 각 도메인 엔드포인트가 내려 주고 있고,
    여기서 또 실으면 같은 데이터가 두 벌이 되어 한쪽만 고쳐지는 순간 어긋난다.
    이 응답이 답하는 건 하나뿐이다 — "그 줄은 전체에서 몇 번째인가".
    """

    model_config = ConfigDict(populate_by_name=True)

    # 화면이 줄을 알아보는 열쇠. 프론트의 rowKeyOf와 같은 꼴이다:
    #   c-{도전장id} / rs-{스냅샷id} / ms-{묶음 첫 경기id}
    key: str
    kind: Literal["challenge", "rankingShift", "gameResultPost"]
    # 아래에서부터 센 번호(가장 오래된 줄이 1). 위에서 세면 새 활동이 하나 올라올 때마다
    # 모든 줄의 번호가 밀린다.
    no: int


class ActivityListOut(BaseModel):
    """활동 화면이 목록을 그리는 데 필요한 것 한 벌 — 줄 번호와 댓글(요청: 단일 API로 통합).

    댓글을 여기 함께 싣는 이유는 두 가지다. 화면 쪽에서 보면 목록 하나를 그리는 데 요청이
    둘이라 어느 하나가 늦거나 실패하면 목록이 반쯤 그려진 채로 남는다 — 실제로 운영에서
    그 두 요청이 나란히 500이었다. 서버 쪽에서 보면 둘은 늘 같은 순간의 같은 화면을 위한
    값이라 따로 받을 이유가 없다.

    카드 내용(경기·도전장·스냅샷)은 여전히 안 싣는다. 그건 저마다 페이지 단위로 나눠
    받아야 하는 것들이고, 여기 실으면 같은 데이터가 두 벌이 되어 한쪽만 고쳐지는 순간
    어긋난다.
    """

    model_config = ConfigDict(populate_by_name=True)

    total: int
    rows: list[ActivityListRow]
    comments: list[ActivityCommentOut] = Field(default_factory=list)
