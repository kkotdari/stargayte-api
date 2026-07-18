from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.app_version.repository import AppVersionRepository
from app.domain.app_version.schemas import AppVersion, AppVersionInfoOut, AppVersionStatusOut


class AppVersionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = AppVersionRepository(session)

    async def get_status(self) -> AppVersionStatusOut:
        state = await self._repo.get_state()
        return AppVersionStatusOut(activeVersion=state.active_version)

    async def list_versions(self) -> list[AppVersionInfoOut]:
        entries = await self._repo.list_versions()
        return [AppVersionInfoOut(number=e.number) for e in entries]

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
        return AppVersionStatusOut(activeVersion=state.active_version)
