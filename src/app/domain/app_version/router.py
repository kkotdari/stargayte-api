from fastapi import APIRouter

from app.api.deps import CurrentAdmin, CurrentMember, DbSession
from app.domain.app_version.schemas import AppVersionSetIn, AppVersionStatusOut
from app.domain.app_version.service import AppVersionService

router = APIRouter(prefix="/app-version", tags=["app-version"])


# 로그인한 회원 누구나 — 랭킹/경기결과/전적통계를 어느 화면 세트(v1/v2)로 그릴지 앱
# 전체가 이 값 하나로 결정한다.
@router.get("", response_model=AppVersionStatusOut)
async def get_app_version(db: DbSession, _member: CurrentMember) -> AppVersionStatusOut:
    return await AppVersionService(db).get_status()


# 관리자(운영자) 아무나 — 합의 절차 없이 바로 전환한다(관리자 패널의 배포/롤백 토글).
@router.put("", response_model=AppVersionStatusOut)
async def set_app_version(
    payload: AppVersionSetIn, db: DbSession, _admin: CurrentAdmin
) -> AppVersionStatusOut:
    return await AppVersionService(db).set_version(payload.active_version)
