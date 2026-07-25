from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

COMMENT_MAX_LENGTH = 50

# 댓글을 달 수 있는 피드 요소 종류 — 새 요소가 생기면 여기에만 추가하면 된다.
FeedTargetType = Literal["match", "challenge"]


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
    target_type: FeedTargetType = Field(alias="targetType")
    target_id: int = Field(alias="targetId")
