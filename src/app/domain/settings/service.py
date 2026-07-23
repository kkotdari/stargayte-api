from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.settings.models import ImageSetting
from app.domain.settings.repository import ImageSettingRepository
from app.domain.settings.schemas import ImageSettingMap, ImageSettingSchema

# constants/races.ts 의 DEFAULT_RACE_ICONS 와 동일한 기본값. DB에 행이 없는 슬롯은
# 이 기본값으로 채워 응답한다 (최초 부팅 시 마이그레이션 시드와도 일치).
DEFAULT_ICONS: dict[str, ImageSettingSchema] = {
    "테란": ImageSettingSchema(type="text", value="T"),
    "프로토스": ImageSettingSchema(type="text", value="P"),
    "저그": ImageSettingSchema(type="text", value="Z"),
    "랜덤": ImageSettingSchema(type="text", value="R"),
}


class ImageSettingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ImageSettingRepository(session)

    async def get_map(self) -> ImageSettingMap:
        rows = await self._repo.list_all()
        result: ImageSettingMap = dict(DEFAULT_ICONS)
        for row in rows:
            # 슬롯 축소(홈 로고 제거) 이전에 저장된 행이 DB에 남아 있을 수 있다 — 더는
            # 유효한 슬롯이 아니면 걸러서 response_model 검증이 깨지지 않게 한다.
            if row.slot not in DEFAULT_ICONS:
                continue
            result[row.slot] = ImageSettingSchema(type=row.icon_type, value=row.icon_value)
        return result

    async def update_map(self, updates: ImageSettingMap, *, actor_pk: int) -> ImageSettingMap:
        for slot, icon in updates.items():
            row = await self._repo.get(slot)
            if row is None:
                self._repo.add(
                    ImageSetting(
                        slot=slot,
                        icon_type=icon.type,
                        icon_value=icon.value,
                        created_by=actor_pk,
                        updated_by=actor_pk,
                    )
                )
            else:
                row.icon_type = icon.type
                row.icon_value = icon.value
                row.updated_by = actor_pk
        await self._session.commit()
        return await self.get_map()
