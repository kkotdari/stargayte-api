from fastapi import APIRouter, Request

from app.api.deps import CurrentAdmin, CurrentMember, DbSession, StorageDep
from app.domain.auth.schemas import (
    AccessHistoryEntry,
    AccessPingRequest,
    AuthResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    SignupRequest,
)
from app.domain.auth.service import AuthService
from app.domain.members.schemas import MemberOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=AuthResponse)
async def login(payload: LoginRequest, request: Request, db: DbSession, storage: StorageDep) -> AuthResponse:
    member, access_token, refresh_token = await AuthService(db, storage).login(
        payload.id,
        payload.password,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return AuthResponse(access_token=access_token, refresh_token=refresh_token, user=MemberOut.model_validate(member))


@router.post("/signup", response_model=AuthResponse)
async def signup(payload: SignupRequest, db: DbSession, storage: StorageDep) -> AuthResponse:
    member, access_token, refresh_token = await AuthService(db, storage).signup(payload)
    return AuthResponse(access_token=access_token, refresh_token=refresh_token, user=MemberOut.model_validate(member))


@router.post("/refresh", response_model=AuthResponse)
async def refresh(payload: RefreshRequest, db: DbSession, storage: StorageDep) -> AuthResponse:
    member, access_token, refresh_token = await AuthService(db, storage).refresh(payload.refresh_token)
    return AuthResponse(access_token=access_token, refresh_token=refresh_token, user=MemberOut.model_validate(member))


@router.post("/logout", status_code=204)
async def logout(payload: LogoutRequest, db: DbSession, storage: StorageDep) -> None:
    await AuthService(db, storage).logout(payload.refresh_token)


@router.get("/me", response_model=MemberOut)
async def me(current: CurrentMember) -> MemberOut:
    # 접속 기록은 여기서 남기지 않는다 — 화면을 전환할 때마다 프론트가 /auth/access-ping을
    # 따로 호출해서 "어떤 화면을 봤는지"까지 남기므로, /me는 세션 복원(사용자 정보 조회)만
    # 담당한다.
    return MemberOut.model_validate(current)


@router.post("/access-ping", status_code=204)
async def access_ping(
    payload: AccessPingRequest, current: CurrentMember, request: Request, db: DbSession, storage: StorageDep
) -> None:
    """로그인된 상태에서 화면(screen)을 전환할 때마다 프론트가 호출 — 어떤 화면을 언제
    봤는지를 접속 기록에 남긴다(같은 화면을 짧은 시간 안에 또 보면 한 행으로 합쳐진다)."""
    await AuthService(db, storage).record_access(
        current,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        screen_code=payload.screen,
    )


@router.get("/access-history", response_model=list[AccessHistoryEntry])
async def access_history(
    db: DbSession, storage: StorageDep, _admin: CurrentAdmin
) -> list[AccessHistoryEntry]:
    return await AuthService(db, storage).list_access_history()
