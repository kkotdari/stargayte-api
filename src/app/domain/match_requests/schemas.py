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


class MatchRequestOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    text: str
    author: MatchRequestAuthor
    created_at: datetime = Field(alias="createdAt")
    recommend_count: int = Field(alias="recommendCount")
    # 지금 조회하는 회원이 이미 추천을 눌렀는지 — 버튼 눌림 상태 표시용.
    recommended_by_me: bool = Field(alias="recommendedByMe")
    # 지금 조회하는 회원이 작성자인지 — 작성자/운영자만 "성사됨" 완료 처리를 할 수 있다.
    mine: bool
    # 언급된 회원들 — 카드에 "언급: A, B"로 표시(권한 등 다른 기능과는 연결 안 함).
    targets: list[MatchRequestTargetOut]


class MatchRequestListOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[MatchRequestOut]
    page: int
    # 페이지당 노출 개수(요청: 5개).
    page_size: int = Field(alias="pageSize")
    total: int
    # 다음 페이지가 더 있는지 — 프론트 페이저용.
    has_more: bool = Field(alias="hasMore")


class MatchRequestCreate(BaseModel):
    # @태그 기능은 폐지됐지만(요청: "@ 기능 완전 제거"), 언급된 사람은 계속 관리한다(요청:
    # "언급된 사람 관리는 하되"). 최소 인원/중복 제한 없이 0명 이상 자유롭게 언급할 수 있고,
    # 언급된 사람에게는 등록 시 알림이 간다. 다른 기능(권한 등)과는 연결하지 않는다.
    text: str = Field(min_length=1, max_length=200)
    target_member_ids: list[str] = Field(
        default_factory=list, alias="targetMemberIds", max_length=20
    )

    model_config = ConfigDict(populate_by_name=True)


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
