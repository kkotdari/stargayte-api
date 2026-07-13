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
    # 거절 사유(선택) — 요청자가 아닌 조회자에게는 항상 None으로 내려간다
    # (to_challenge_out의 viewer_pk 검사 참고).
    reject_reason: str | None = Field(default=None, alias="rejectReason")


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
    response: Literal["accepted", "rejected"]
    # 거절할 때만 의미가 있다(선택) — 승락에 사유를 보내도 그냥 무시된다.
    reason: str | None = None


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
