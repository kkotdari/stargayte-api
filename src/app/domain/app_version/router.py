from fastapi import APIRouter, status

from app.api.deps import CurrentAdmin, CurrentMember, DbSession
from app.domain.app_version.schemas import (
    AppVersion,
    AppVersionAddIn,
    AppVersionInfoOut,
    AppVersionNotesIn,
    AppVersionSetIn,
    AppVersionStatusOut,
    VersionNoticeSettingsOut,
    VersionNoticeToggleIn,
)
from app.domain.app_version.service import AppVersionService

router = APIRouter(prefix="/app-version", tags=["app-version"])
# 등록된 버전 목록은 컬렉션이라 복수형 경로(/app-versions)로 분리한다.
registry_router = APIRouter(prefix="/app-versions", tags=["app-version"])


# 로그인한 회원 누구나 — 랭킹/경기결과/전적통계를 어느 화면 세트로 그릴지 앱 전체가 이 값
# 하나로 결정한다. 버전 안내 표시 토글(noticeEnabled)도 함께 내려준다.
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
# 이 목록을 쓰고, 버전별 안내 내용(notes)도 여기에 함께 담겨 내려온다).
@registry_router.get("", response_model=list[AppVersionInfoOut])
async def list_app_versions(db: DbSession, _member: CurrentMember) -> list[AppVersionInfoOut]:
    return await AppVersionService(db).list_versions()


# 새 버전 등록 — 관리자만. 버전 관리 모달에서 자유 숫자 입력으로 추가한다(중복/형식은 서버 검증).
@registry_router.post("", response_model=AppVersionInfoOut, status_code=status.HTTP_201_CREATED)
async def add_app_version(
    payload: AppVersionAddIn, db: DbSession, _admin: CurrentAdmin
) -> AppVersionInfoOut:
    return await AppVersionService(db).add_version(payload.number)


# 등록된 버전 삭제 — 관리자만. 활성 버전/마지막 한 개는 지울 수 없다(서버에서 막는다).
@registry_router.delete("/{number}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_app_version(number: AppVersion, db: DbSession, _admin: CurrentAdmin) -> None:
    await AppVersionService(db).delete_version(number)


# 버전 안내 표시 여부(전역 토글) — 관리자 패널의 "버전 안내 설정"에서 켜고 끈다.
@registry_router.put("/notice-settings", response_model=VersionNoticeSettingsOut)
async def set_notice_settings(
    payload: VersionNoticeToggleIn, db: DbSession, _admin: CurrentAdmin
) -> VersionNoticeSettingsOut:
    return await AppVersionService(db).set_notice_enabled(payload.enabled)


# 특정 버전의 안내 내용 편집 — 관리자만. 경로의 버전은 등록돼 있어야 한다.
@registry_router.put("/{number}/notes", response_model=AppVersionInfoOut)
async def set_app_version_notes(
    number: AppVersion, payload: AppVersionNotesIn, db: DbSession, _admin: CurrentAdmin
) -> AppVersionInfoOut:
    return await AppVersionService(db).set_notes(number, payload.notes)
