from fastapi import APIRouter

from app.api.deps import CurrentAdmin, CurrentMember, DbSession
from app.domain.app_version.schemas import AppVersionInfoOut, AppVersionSetIn, AppVersionStatusOut
from app.domain.app_version.service import AppVersionService

router = APIRouter(prefix="/app-version", tags=["app-version"])
# 등록된 버전 목록은 컬렉션이라 복수형 경로(/app-versions)로 분리한다.
registry_router = APIRouter(prefix="/app-versions", tags=["app-version"])


# 로그인한 회원 누구나 — 랭킹/경기결과/전적통계를 어느 화면 세트로 그릴지 앱 전체가 이 값
# 하나로 결정한다.
@router.get("", response_model=AppVersionStatusOut)
async def get_app_version(db: DbSession, _member: CurrentMember) -> AppVersionStatusOut:
    return await AppVersionService(db).get_status()


# 관리자(운영자) 아무나 — 합의 절차 없이 바로 전환한다. 단, 등록된 버전으로만 가능하다.
@router.put("", response_model=AppVersionStatusOut)
async def set_app_version(
    payload: AppVersionSetIn, db: DbSession, _admin: CurrentAdmin
) -> AppVersionStatusOut:
    return await AppVersionService(db).set_version(payload.active_version)


# 관리자 패널의 버전 선택 팝업이 나열할 '등록된 버전' 목록 — 회원 누구나 조회 가능(미리보기도
# 이 목록을 쓴다).
@registry_router.get("", response_model=list[AppVersionInfoOut])
async def list_app_versions(db: DbSession, _member: CurrentMember) -> list[AppVersionInfoOut]:
    return await AppVersionService(db).list_versions()
