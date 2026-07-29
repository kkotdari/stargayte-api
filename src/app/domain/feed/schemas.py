from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

COMMENT_MAX_LENGTH = 50

# 댓글을 달 수 있는 피드 요소 종류 — 새 요소가 생기면 여기에만 추가하면 된다.
FeedTargetType = Literal["gameResult", "challenge", "rankingShift"]

# 이름 통일(요청) 전에 쓰던 값 → 지금 값. 이 값은 feed_comments.target_type에 그대로
#저장되므로 부팅 때 한 번 일괄로 옮기지만(_migrate_feed_target_types), 배포가 어긋난
# 순간의 옛 프론트가 옛 값을 보낼 수 있어 받는 쪽에서도 계속 받아 준다.
LEGACY_FEED_TARGET_TYPES = {"match": "gameResult", "rankshift": "rankingShift"}

# 요청으로 들어오는 값 — 위 이유로 옛 이름까지 허용하고, normalize_target_type으로 새
# 이름 하나로 모아서 저장/조회한다.
FeedTargetTypeInput = Literal["gameResult", "challenge", "rankingShift", "match", "rankshift"]


def normalize_target_type(value: str) -> str:
    return LEGACY_FEED_TARGET_TYPES.get(value, value)


class FeedCommentAuthor(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    member_id: str = Field(alias="memberId")
    nickname: str
    avatar: str | None = None


class FeedCommentMentionOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    member_id: str = Field(alias="memberId")
    nickname: str


class FeedCommentOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    target_type: FeedTargetType = Field(alias="targetType")
    target_id: int = Field(alias="targetId")
    text: str
    author: FeedCommentAuthor
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    can_edit: bool = Field(alias="canEdit")
    mentions: list[FeedCommentMentionOut]


class FeedCommentWrite(BaseModel):
    """댓글 작성/수정 공용 페이로드."""

    model_config = ConfigDict(populate_by_name=True)

    text: str = Field(min_length=1, max_length=COMMENT_MAX_LENGTH)
    target_member_ids: list[str] = Field(
        default_factory=list, alias="targetMemberIds", max_length=20
    )


class FeedCommentCreate(FeedCommentWrite):
    target_type: FeedTargetTypeInput = Field(alias="targetType")
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


class RankingShiftOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    match_type: Literal["0101", "0102"] = Field(alias="matchType")
    reason: str
    created_at: datetime = Field(alias="createdAt")
    match_ids: list[int] = Field(alias="matchIds")
    shifts: list[RankingShiftEntry]
