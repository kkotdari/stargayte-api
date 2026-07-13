from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.app_version.repository import AppVersionRepository
from app.domain.app_version.schemas import AppVersion, AppVersionStatusOut


class AppVersionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = AppVersionRepository(session)

    async def get_status(self) -> AppVersionStatusOut:
        state = await self._repo.get_state()
        return AppVersionStatusOut(activeVersion=state.active_version)

    async def set_version(self, version: AppVersion) -> AppVersionStatusOut:
        state = await self._repo.get_state()
        state.active_version = version
        await self._session.commit()
        return AppVersionStatusOut(activeVersion=state.active_version)
