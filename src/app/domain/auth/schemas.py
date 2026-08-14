from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.members.schemas import MemberOut

# 프론트엔드 ScreenKey(App.tsx)와 동일한 값 — app.domain.auth.models.SCREEN_CODES 참고.
# official/accessHistory/menuPermissions/userMapping은 예전 화면 이름 체계의 흔적으로
# 지금 프론트는 안 보내지만(과거 접속 기록과의 호환을 위해 그대로 둔다), challenge(너
# 나와!)/gameId(게임아이디)는 나중에 추가된 실제 화면인데 여기 누락돼 있었다 — 그 값으로
# 오는 pingAccess가 검증 단계(422)에서 그대로 막혀 조용히 기록이 안 됐다(요청: "접속
# 이력 남길때 새 메뉴인 너 나와의 코드가 안들어가는거 같음").
# 지금 실제로 존재하는 화면(프론트 types/index.ts의 ScreenKey)만 남긴다(요청: "feed
# stats user league 이런거 남은거만 사용하게 정리"). 지금까지는 없어진 화면 코드
# (ranking·official·imageSettings·menuPermissions·userMapping·accessHistory·gameId)가
# 계속 남아 있는 반면 정작 홈인 feed가 빠져 있어, 피드 진입 핑이 422로 조용히 막혔다.
# 여기 목록이 유일한 기준이다 — 화면이 늘면 프론트 ScreenKey와 함께 이 줄만 고친다
# (예전엔 DB CHECK 제약이 같은 목록을 이중으로 들고 있었는데, 스키마를 create_all로만
# 관리해 기존 DB의 제약이 갱신되지 않아 새 화면마다 조용히 막히는 사고가 반복됐다 —
# models.py에서 제약을 걷어내고 검증은 이 한 곳으로 모았다).
ScreenCode = Literal[
    # "feed"는 옛 이름이다 — 화면을 '활동'으로 바꿨지만(요청), 프론트와 서버는 따로
    # 배포되므로 새 서버가 먼저 뜨는 동안 아직 옛 프론트가 "feed"를 보낸다. 둘 다 받는다.
    "activity", "feed", "match", "challenge", "stats", "members", "leagues", "rivalry",
    # 통계("stats")가 둘로 갈렸다(요청: 래더와 내전은 메뉴 진입점부터 분리) — 래더는
    # 일대일 리더보드, 내전은 팀전 통계다. "stats"는 위에 그대로 남겨 둔다: 프론트와 서버가
    # 따로 배포되므로 새 서버가 먼저 뜨는 동안 아직 옛 프론트가 그 코드를 보낸다.
    "ladder", "clan",
    # 운영 메뉴로 들어온 화면들 — 프론트 ScreenKey에 추가되고도 여기 빠져 있어 진입 핑이
    # 422로 막혔다(브라우저 콘솔에 그대로 찍혔다). 화면이 늘면 이 줄을 함께 고쳐야 한다.
    "minimaps", "control",
    # 자료실 > 모델링 갤러리(지적: 화면 코드가 없어 진입 이력이 422로 막혔다).
    "models",
    # 공유 링크(?sv=…&sid=…)로 열린 카드 한 장짜리 화면(요청: "접속로그에 공유페이지
    # 열어본거도 표시(어떤 페이지인지도)") — 어느 카드였는지는 detail이 따로 적는다.
    "share",
    # 화면 이동이 아니라 로그인 자체 — 예전엔 NULL로 남겼는데 목록에서 구분이 안 돼
    # 명시적인 코드로 남긴다(요청: "단순 로그인도 login 으로").
    "login",
]


class LoginRequest(BaseModel):
    id: str
    password: str


class SignupRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=4, max_length=128)
    battletag: str = Field(min_length=1, max_length=50)
    # 리플레이(.rep)에 실제로 기록되는 게임 내 표시 이름 — 가입 화면에서 이 항목을 뺐다
    # (요청: "회원가입 모달에서 게임아이디 항목 삭제"). 관리자가 회원을 직접 만들 때
    # (MemberCreate)와 마찬가지로 0개로 시작하고, 필요하면 가입 후 내 정보 수정에서
    # 언제든 추가할 수 있다.
    replay_aliases: list[str] = Field(default_factory=list, alias="replayAliases")
    insta: str = ""
    avatar: str | None = None


class AuthResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    access_token: str = Field(alias="accessToken")
    refresh_token: str = Field(alias="refreshToken")
    token_type: str = Field(default="bearer", alias="tokenType")
    user: MemberOut


class RefreshRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    refresh_token: str = Field(alias="refreshToken")


class LogoutRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    refresh_token: str = Field(alias="refreshToken")


class AccessPingRequest(BaseModel):
    """프론트엔드가 화면(screen)을 전환할 때마다 보내는 접속 기록 핑.

    detail은 그 화면 안에서 정확히 무엇을 봤는지다 — 지금은 공유 링크로 열린 카드
    ("gameResult#12", "challenge#7", "stack#2026-07-29")를 적는 데만 쓴다(요청: "어떤
    페이지인지도"). 화면 코드만으로는 공유로 들어온 사람이 무엇을 열어 봤는지 알 수 없다.
    자유 문자열이라 값 자체는 검증하지 않고 길이만 막는다."""

    screen: ScreenCode
    detail: str | None = Field(default=None, max_length=64)


class AccessHistoryEntry(BaseModel):
    """관리자 전용 접속 기록 한 건. /auth/login(screen="login")과, 화면을 전환할 때마다 오는
    /auth/access-ping(해당 화면 코드) 양쪽에서 기록된다. 같은 사람이 같은 화면을 짧은 시간
    부를 때마다 한 행씩 그대로 쌓인다 — 합치지 않는다(AuthService.record_access 참고).
    IP/기기 정보는 개인정보라 관리 화면에 노출하지 않으므로 이 응답에도 포함하지 않는다."""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    member_id: str = Field(alias="memberId")
    member_nickname: str = Field(alias="memberNickname")
    logged_in_at: datetime = Field(alias="loggedInAt")
    # 입력(ScreenCode)과 달리 출력은 느슨한 str — 이미 쌓인 옛 화면 코드(ranking·official·
    # gameId 등)와 로그인 NULL 행이 그대로 남아 있어서, 현재 목록으로 좁히면 조회가
    # 응답 검증에서 통째로 500이 난다.
    screen_code: str | None = Field(alias="screenCode")
    # 공유 링크로 열린 카드가 무엇이었는지 — 그 외 화면은 None.
    detail: str | None = None
