from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.settings.models import ImageSetting


class ImageSettingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[ImageSetting]:
        stmt = select(ImageSetting)
        return list((await self._session.execute(stmt)).scalars().all())

    async def get(self, slot: str) -> ImageSetting | None:
        return await self._session.get(ImageSetting, slot)

    def add(self, icon: ImageSetting) -> None:
        self._session.add(icon)
