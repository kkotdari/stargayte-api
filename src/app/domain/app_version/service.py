from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.app_version.repository import AppVersionRepository
from app.domain.app_version.schemas import (
    AppVersion,
    AppVersionInfoOut,
    AppVersionStatusOut,
    VersionNoticeSettingsOut,
)
from app.domain.env_vars.repository import EnvVarRepository

# 버전 안내(업데이트 안내 모달) 전역 표시 여부 — env_vars 테이블의 key. 행이 없으면 켜짐으로
# 본다(앱 기본값): 배포 때마다 안내를 띄우던 기존 동작을 그대로 유지하고, 관리자가 끄면
# 그때 "false" 행이 생긴다.
VERSION_NOTICE_ENABLED_KEY = "version_notice_enabled"


def _entry_out(entry) -> AppVersionInfoOut:
    return AppVersionInfoOut(number=entry.number, notes=entry.notes or "")


class AppVersionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = AppVersionRepository(session)
        self._env = EnvVarRepository(session)

    async def _notice_enabled(self) -> bool:
        value = await self._env.get_value(VERSION_NOTICE_ENABLED_KEY)
        # 행이 없으면(None) 켜짐이 기본. 명시적으로 "false"일 때만 끈다.
        return value != "false"

    async def get_status(self) -> AppVersionStatusOut:
        state = await self._repo.get_state()
        return AppVersionStatusOut(
            activeVersion=state.active_version,
            noticeEnabled=await self._notice_enabled(),
        )

    async def list_versions(self) -> list[AppVersionInfoOut]:
        entries = await self._repo.list_versions()
        return [_entry_out(e) for e in entries]

    async def set_version(self, version: AppVersion) -> AppVersionStatusOut:
        # 등록되지 않은 버전으로는 배포할 수 없다(요청: "등록된 버전만"). 프론트가 등록된
        # 목록에서만 고르게 하지만, 서버에서도 한 번 더 막는다.
        if not await self._repo.version_registered(version):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="등록되지 않은 버전이에요.",
            )
        state = await self._repo.get_state()
        state.active_version = version
        await self._session.commit()
        return AppVersionStatusOut(
            activeVersion=state.active_version,
            noticeEnabled=await self._notice_enabled(),
        )

    async def set_notice_enabled(self, enabled: bool) -> VersionNoticeSettingsOut:
        await self._env.set_value(VERSION_NOTICE_ENABLED_KEY, "true" if enabled else "false")
        await self._session.commit()
        return VersionNoticeSettingsOut(enabled=enabled)

    async def set_notes(self, number: AppVersion, notes: str) -> AppVersionInfoOut:
        entry = await self._repo.get_entry(number)
        if entry is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="등록되지 않은 버전이에요.",
            )
        # 앞뒤 공백/빈 줄만 다듬어 저장하고, 완전히 비면 NULL로 둔다(그 버전은 안내 안 띄움).
        cleaned = notes.strip()
        entry.notes = cleaned or None
        await self._session.commit()
        return _entry_out(entry)
