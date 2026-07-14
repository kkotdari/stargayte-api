from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TargetResponse = Literal["pending", "accepted", "rejected"]
# 목록/폼 어디서도 회원이 직접 고르지 않는다 — 지목 인원수로 서버가 정한다(1명=1:1, 2명↑=팀전).
ChallengeMatchType = Literal["0101", "0102"]
ChallengeStatus = Literal["pending", "confirmed", "rejected", "canceled"]


class ChallengeAuthor(BaseModel):
    id: str
    nickname: str


class ChallengeTargetOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    member_id: str = Field(alias="memberId")
    nickname: str
    battletag: str
    avatar: str | None
    response: TargetResponse
    # 응답(수락/거절) 한마디 — 전체 공개다(요청자가 아니어도 누구나 볼 수 있다). 아직
    # 응답 안 했으면(response="pending") None.
    response_message: str | None = Field(default=None, alias="responseMessage")


class ChallengeOwnMemberOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    member_id: str = Field(alias="memberId")
    nickname: str
    battletag: str
    avatar: str | None


class ChallengeOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    match_type: ChallengeMatchType = Field(alias="matchType")
    scheduled_at: datetime | None = Field(alias="scheduledAt")
    message: str
    status: ChallengeStatus
    created_by: ChallengeAuthor = Field(alias="createdBy")
    targets: list[ChallengeTargetOut]
    own_members: list[ChallengeOwnMemberOut] = Field(alias="ownMembers")
    result_match_id: int | None = Field(alias="resultMatchId")
    created_at: datetime = Field(alias="createdAt")


class ChallengeCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    scheduled_at: datetime | None = Field(default=None, alias="scheduledAt")
    message: str = ""
    target_member_ids: list[str] = Field(alias="targetMemberIds", min_length=1, max_length=4)
    # 도전자 본인은 자동 포함(뺄 수 없음)이라 여기엔 "본인 제외 나머지 내 팀원"만 담는다
    # — 본인 포함 최대 4명이라 이 목록 자체는 최대 3명. (지금 UI는 1:1만 신청하므로 항상
    # 빈 배열로 오지만, 서버는 계속 팀전을 받아준다 — 나중에 UI가 팀전을 다시 열면 그대로 쓴다.)
    own_team_member_ids: list[str] = Field(default_factory=list, alias="ownTeamMemberIds", max_length=3)

    @model_validator(mode="after")
    def _normalize(self) -> "ChallengeCreate":
        if len(set(self.target_member_ids)) != len(self.target_member_ids):
            raise ValueError("같은 회원을 두 번 지목할 수 없습니다.")
        if len(set(self.own_team_member_ids)) != len(self.own_team_member_ids):
            raise ValueError("같은 회원을 두 번 지목할 수 없습니다.")
        if set(self.target_member_ids) & set(self.own_team_member_ids):
            raise ValueError("상대 팀과 내 팀에 같은 회원을 동시에 넣을 수 없습니다.")
        return self


class ChallengeRespondIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    response: Literal["accepted", "rejected"]
    # 응답 한마디 — API 자체는 선택으로 둔다(경기결과 화면 목록의 빠른 승락/거절
    # 버튼은 메시지 없이 한 번에 응답하는 흐름을 그대로 유지해야 한다). "필수화" 요청은
    # 인박스(편지지) 화면에서만 적용되고, 그쪽은 프론트에서 빈 값이면 제출 버튼 자체를
    # 막는다(ChallengeInboxModal.tsx 참고).
    reason: str | None = None
    # 도전장 작성 시 "시간 지정"을 끄면(scheduled_at=None) "상대가 정해도 된다"는
    # 뜻인데, 그 이후 아무도 시간을 채워 넣을 방법이 없어서 시간 미정인 채로 영원히
    # "승락" 상태에 박제되는 문제가 있었다(요청: "도전자/상대 모두 시간을 지정하지
    # 않았는데 수락이 된 경우가 있네 이러면 안되는데") — 원래 의도대로 상대가 수락하는
    # 시점에 이걸로 시간을 정하게 한다. 이미 시간이 정해진 도전장에는 서비스 레이어에서
    # 무시한다(응답하는 쪽이 요청자가 정한 시간을 바꿀 수는 없다).
    scheduled_at: datetime | None = Field(default=None, alias="scheduledAt")


class ChallengeReapplyIn(BaseModel):
    """거절된 도전장을 재신청 — 시간/메모를 비우면(None) 기존 값을 그대로 유지한다."""

    model_config = ConfigDict(populate_by_name=True)

    scheduled_at: datetime | None = Field(default=None, alias="scheduledAt")
    message: str | None = None


class ChallengeAttachResultIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    match_id: int = Field(alias="matchId")


class ChallengeListOut(BaseModel):
    items: list[ChallengeOut]
