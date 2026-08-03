from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

COMMENT_MAX_LENGTH = 50

# 댓글을 달 수 있는 활동 요소 종류 — 새 요소가 생기면 여기에만 추가하면 된다.
ActivityTargetType = Literal["gameResult", "challenge", "rankingShift"]

# 이름 통일(요청) 전에 쓰던 값 → 지금 값. 이 값은 activity_comments.target_type에 그대로
#저장되므로 부팅 때 한 번 일괄로 옮기지만(_migrate_feed_target_types), 배포가 어긋난
# 순간의 옛 프론트가 옛 값을 보낼 수 있어 받는 쪽에서도 계속 받아 준다.
LEGACY_FEED_TARGET_TYPES = {"match": "gameResult", "rankshift": "rankingShift"}

# 요청으로 들어오는 값 — 위 이유로 옛 이름까지 허용하고, normalize_target_type으로 새
# 이름 하나로 모아서 저장/조회한다.
ActivityTargetTypeInput = Literal["gameResult", "challenge", "rankingShift", "match", "rankshift"]


def normalize_target_type(value: str) -> str:
    return LEGACY_FEED_TARGET_TYPES.get(value, value)


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
