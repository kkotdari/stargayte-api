from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# "discarded" = 지목된 상대가 편지봉투를 열지 않고 사유 없이 "버림"(휴지통행) — 사유가 있는
# 명시적 "rejected"(거절)와 구분한다.
TargetResponse = Literal["pending", "accepted", "rejected", "discarded"]
# 목록/폼 어디서도 회원이 직접 고르지 않는다 — 지목 인원수로 서버가 정한다(1명=1:1, 2명↑=팀전).
ChallengeMatchType = Literal["0101", "0102"]
# 4개 상태만 있다 — 응답대기(pending)/성사(confirmed, 대결 대기)/완료(done)/폐기(discarded,
# 휴지통). 거절·무응답·미실시·(레거시)취소는 모두 폐기로 통합됐다.
ChallengeStatus = Literal["pending", "confirmed", "done", "discarded"]
# 확정 대결의 결과 — 이긴 쪽(creator/target) 외에 무승부(draw)/미실시(not_held)도 있다.
# not_held(미실시)는 완료가 아니라 폐기(휴지통)로 간다.
ChallengeResult = Literal["creator", "target", "draw", "not_held"]


# 편지지 배경 사진 한 장의 상한 — data URL 문자열 길이다(base64라 실제 바이트의 약 4/3).
# 브라우저가 긴 변 1440px JPEG로 줄여 보내므로 보통 한 장에 200~500KB다.
_IMAGE_MAX_CHARS = 3_000_000


class ChallengeAuthor(BaseModel):
    id: str
    nickname: str
    avatar: str | None = None


class ChallengeTargetOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    member_id: str = Field(alias="memberId")
    nickname: str
    battletag: str
    avatar: str | None
    response: TargetResponse
    # 이 대상이 응답하며 남긴 "한마디"(선택) — 없으면 빈 문자열.
    response_message: str = Field(default="", alias="responseMessage")


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
    # 도전자가 호출 때 남긴 "한마디"(선택) — 없으면 빈 문자열.
    message: str = ""
    # 정렬/날짜 그룹핑/카운트다운용 파생 일시(UTC) — 시간이 미정이면 자정으로 채워 내려간다.
    scheduled_at: datetime | None = Field(alias="scheduledAt")
    # 실제 저장값 — 날짜 하나뿐이다(시각 필드는 없앴다).
    scheduled_date: str | None = Field(default=None, alias="scheduledDate")
    # 시간을 사람 말로 적어 둔 것(요청) — "저녁 9시쯤" 같은 자유 텍스트. 안 적었으면 빈 문자열.
    # 정렬/마감 계산에는 안 쓴다(그건 계속 scheduledDate만 본다).
    scheduled_time_note: str = Field(default="", alias="scheduledTimeNote")
    status: ChallengeStatus
    created_by: ChallengeAuthor = Field(alias="createdBy")
    targets: list[ChallengeTargetOut]
    own_members: list[ChallengeOwnMemberOut] = Field(alias="ownMembers")
    created_at: datetime = Field(alias="createdAt")
    # 마지막으로 손댄 시각 — 응답(수락/거절/버림), 일시 수정, 결과 입력, 취소가 전부 여기
    # 찍힌다(TimestampMixin의 onupdate). 활동 목록이 "새로 올라온 것(NEW)"과 "달라진
    # 것(UPDATE)"을 가르는 데 쓴다 — 만든 지는 오래됐지만 방금 답이 온 호출은 새것이
    # 아니라 달라진 것이다.
    updated_at: datetime = Field(alias="updatedAt")
    # 폐기(휴지통)된 시각 — 폐기 상태가 아니면 None. 휴지통 목록을 "최근 버려진 순"으로
    # 정렬하는 데 쓴다(프론트 요청: "최근 버려진게 위에 오게").
    discarded_at: datetime | None = Field(default=None, alias="discardedAt")
    # 그 폐기가 '취소'였다면 취소한 사람 — 아니면 None(상대의 거절·버림, 무응답 만료,
    # 미실시). 화면은 이 값으로 "취소"와 "만료"를 갈라 그 사람 자리에 표시한다(요청).
    canceled_by: ChallengeAuthor | None = Field(default=None, alias="canceledBy")
    # 확정된 대결의 결과(이긴 쪽) — 아직 아무도 입력하지 않았으면 None.
    result_winner_side: ChallengeResult | None = Field(default=None, alias="resultWinnerSide")
    # 편지지 배경 사진(선택) — 없으면 None이고, 그러면 편지지는 평소의 유리 그대로다.
    backdrop_url: str | None = Field(default=None, alias="backdropUrl")
    # 같은 사진에 로고·문구를 얹은 카카오 공유 카드판 — 사진의 원래 비율 그대로다.
    backdrop_share_url: str | None = Field(default=None, alias="backdropShareUrl")
    # 그 판의 실제 크기(px) — 카카오에 함께 넘겨야 원래 비율로 앉는다.
    backdrop_share_width: int | None = Field(default=None, alias="backdropShareWidth")
    backdrop_share_height: int | None = Field(default=None, alias="backdropShareHeight")


class ChallengeCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # 날짜/시간 각각 선택(요청: 시간 null 가능, 날짜만 지정 가능). 둘은 서로 상관없다
    # (요청: "둘은 이제 상관없이 별도로 입력가능") — 날짜 없이 "이번 주말쯤"만 적어 보낼
    # 수도 있다. 예전엔 날짜가 없으면 "언제"를 버렸는데, 그러면 사람이 적어 넣은 말이
    # 소리 없이 사라졌다.
    scheduled_date: date | None = Field(default=None, alias="scheduledDate")
    # 약속 시간을 사람 말로(요청: "시간 추가하기"를 누르면 한마디처럼) — 한글 30자 제한.
    scheduled_time_note: str = Field(default="", max_length=30, alias="scheduledTimeNote")
    # 호출 한마디(선택) — 한글 50자 제한(요청).
    message: str = Field(default="", max_length=50)
    target_member_ids: list[str] = Field(alias="targetMemberIds", min_length=1, max_length=4)
    # 도전자 본인은 자동 포함(뺄 수 없음)이라 여기엔 "본인 제외 나머지 내 팀원"만 담는다
    # — 본인 포함 최대 4명이라 이 목록 자체는 최대 3명. (지금 UI는 1:1만 신청하므로 항상
    # 빈 배열로 오지만, 서버는 계속 팀전을 받아준다 — 나중에 UI가 팀전을 다시 열면 그대로 쓴다.)
    own_team_member_ids: list[str] = Field(default_factory=list, alias="ownTeamMemberIds", max_length=3)
    # 편지지 배경 사진(선택) — 브라우저가 캔버스로 줄여서 data URL로 보낸다(요청: "용량
    # 줄여서 업로드"). 서버는 받은 뒤 한 번 더 줄여 저장하므로 이 상한은 "말도 안 되게
    # 큰 것"만 걸러내는 자리다.
    backdrop: str | None = Field(default=None, max_length=_IMAGE_MAX_CHARS)
    # 같은 사진에 로고·문구를 얹은 공유 카드판 — 브라우저가 함께 만들어 보낸다.
    backdrop_share: str | None = Field(
        default=None, max_length=_IMAGE_MAX_CHARS, alias="backdropShare",
    )

    @model_validator(mode="after")
    def _normalize(self) -> "ChallengeCreate":
        for name, value in (("backdrop", self.backdrop), ("backdropShare", self.backdrop_share)):
            if value is not None and not value.startswith("data:image/"):
                raise ValueError(f"{name}은(는) data:image/... 형식이어야 합니다.")
        if len(set(self.target_member_ids)) != len(self.target_member_ids):
            raise ValueError("같은 회원을 두 번 지목할 수 없습니다.")
        if len(set(self.own_team_member_ids)) != len(self.own_team_member_ids):
            raise ValueError("같은 회원을 두 번 지목할 수 없습니다.")
        if set(self.target_member_ids) & set(self.own_team_member_ids):
            raise ValueError("상대 팀과 내 팀에 같은 회원을 동시에 넣을 수 없습니다.")
        return self


class ChallengeRespondIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # discarded = 사유 없이 버림(휴지통행) — 편지봉투의 "버리기"에서 온다.
    response: Literal["accepted", "rejected", "discarded"]
    # 응답 한마디(선택) — 한글 50자 제한(요청).
    message: str = Field(default="", max_length=50)
    # 요청자가 일정을 안 정하고(날짜/시간 미정) 보냈으면, 상대가 수락하는 이 시점에 날짜/시간을
    # 정할 수 있다 — 둘 다 선택이라 안 정한 채 수락도 가능하다(요청: "시간 미선택 수락 가능",
    # "날짜만 지정하고 시간은 나중에도 가능"). 이미 요청자가 일정을 정한 도전장이면 서비스
    # 레이어에서 무시한다(응답하는 쪽이 바꿀 수 없다).
    scheduled_date: date | None = Field(default=None, alias="scheduledDate")
    # 약속 시간을 사람 말로(요청: "시간 추가하기"를 누르면 한마디처럼) — 한글 30자 제한.
    scheduled_time_note: str = Field(default="", max_length=30, alias="scheduledTimeNote")


class ChallengeRescheduleIn(BaseModel):
    """성사(confirmed)된 대결의 일시를 나중에 바꾼다 — 참가자 또는 운영자만(서비스에서
    검증). 날짜/시간 모두 선택이라 미정으로 되돌릴 수도 있다(요청: "제약 없이 다 열어두기")."""

    model_config = ConfigDict(populate_by_name=True)

    scheduled_date: date | None = Field(default=None, alias="scheduledDate")
    # 약속 시간을 사람 말로(요청: "시간 추가하기"를 누르면 한마디처럼) — 한글 30자 제한.
    scheduled_time_note: str = Field(default="", max_length=30, alias="scheduledTimeNote")


class ChallengeResultIn(BaseModel):
    """확정된 대결의 결과 입력 — 참가자 누구든(도전자편/상대편 상관없이) 먼저 입력하는
    쪽이 그대로 인정된다. 이미 결과가 입력된 대결에는 다시 입력할 수 없다. 결과 입력 시엔
    실제 대결 날짜를 무조건 함께 넣는다(시각은 필드 자체가 없어졌다 — 너 나와는 날짜만 다룬다)."""

    model_config = ConfigDict(populate_by_name=True)

    winner_side: ChallengeResult = Field(alias="winnerSide")
    scheduled_date: date = Field(alias="scheduledDate")


class ChallengeListOut(BaseModel):
    items: list[ChallengeOut]
