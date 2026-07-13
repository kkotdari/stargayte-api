from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.members.schemas import MemberOut

# 프론트엔드 ScreenKey(App.tsx)와 동일한 값 — app.domain.auth.models.SCREEN_CODES 참고.
ScreenCode = Literal[
    "ranking", "match", "official", "stats", "members", "accessHistory",
    "imageSettings", "menuPermissions", "userMapping",
]


class LoginRequest(BaseModel):
    id: str
    password: str


class SignupRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=4, max_length=128)
    battletag: str = Field(min_length=1, max_length=50)
    # 리플레이(.rep)에 실제로 기록되는 게임 내 표시 이름 — 리플레이 일괄 등록 매칭에 꼭
    # 필요해서 가입 시점부터 최소 1개는 필수로 받는다. 개수 제한은 없다 — 가입 화면에서도
    # 처음부터 여러 개를 받을 수 있다.
    replay_aliases: list[str] = Field(min_length=1, alias="replayAliases")
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
