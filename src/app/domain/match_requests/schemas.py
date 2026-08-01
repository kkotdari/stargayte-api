from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MatchRequestAuthor(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    member_id: str = Field(alias="memberId")
    nickname: str
    avatar: str | None = None


class MatchRequestTargetOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    member_id: str = Field(alias="memberId")
    nickname: str


class MatchRequestInboxItem(BaseModel):
    """언급 알림 한 건 — 앱 열 때 인박스 팝업에 뜬다."""

    model_config = ConfigDict(populate_by_name=True)

    request_id: int = Field(alias="requestId")
    text: str
    author: MatchRequestAuthor
    created_at: datetime = Field(alias="createdAt")
    # 이 요청에 함께 언급된 사람들(나 포함) — 팝업에 누구누구가 언급됐는지 보여준다.
    mentioned: list[MatchRequestTargetOut]


class MatchRequestInboxOut(BaseModel):
    items: list[MatchRequestInboxItem]
