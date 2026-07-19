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
    # 지금 조회하는 회원이 이 요청의 작성자인지 — 본인 글엔 "들어주기" 대신 "내림" 만 보인다.
    mine: bool
    # @태그로 지목된 회원들 — 본문 하이라이트/렌더용.
    targets: list[MatchRequestTargetOut]
    # 지금 조회하는 회원이 지목된 대상인지 — 이 사람만 "들어주기" 버튼을 누를 수 있다.
    i_am_target: bool = Field(alias="iAmTarget")


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
    text: str = Field(min_length=1, max_length=200)
    # 본문에 @태그로 지목한 회원들의 로그인 아이디 — 최소 2명(요청). 이 사람들만 들어줄 수 있다.
    target_member_ids: list[str] = Field(alias="targetMemberIds", min_length=2, max_length=20)

    model_config = ConfigDict(populate_by_name=True)
