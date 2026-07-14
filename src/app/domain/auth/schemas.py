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
ScreenCode = Literal[
    "ranking", "match", "official", "stats", "members", "accessHistory",
    "imageSettings", "menuPermissions", "userMapping", "challenge", "gameId",
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
    """프론트엔드가 화면(screen)을 전환할 때마다 보내는 접속 기록 핑."""

    screen: ScreenCode


class AccessHistoryEntry(BaseModel):
    """관리자 전용 접속 기록 한 건. /auth/login(screen=NULL)과, 화면을 전환할 때마다 오는
    /auth/access-ping(해당 화면 코드) 양쪽에서 기록된다. 같은 사람이 같은 화면을 짧은 시간
    안에 다시 조회하면 새 행 대신 그 행의 시각만 갱신한다(AuthService.record_access 참고).
    IP/기기 정보는 개인정보라 관리 화면에 노출하지 않으므로 이 응답에도 포함하지 않는다."""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    member_id: str = Field(alias="memberId")
    member_nickname: str = Field(alias="memberNickname")
    logged_in_at: datetime = Field(alias="loggedInAt")
    screen_code: ScreenCode | None = Field(alias="screenCode")
