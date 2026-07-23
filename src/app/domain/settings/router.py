from fastapi import APIRouter

from app.api.deps import CurrentAdmin, DbSession
from app.domain.settings.schemas import ImageSettingMap
from app.domain.settings.service import ImageSettingService

router = APIRouter(prefix="/settings/image-settings", tags=["settings"])


# (홈 로고 슬롯은 제거됐지만 조회는 공개로 유지 — 민감정보 없는 종족 아이콘뿐이고,
# 공개→인증 강화는 호환성만 깨뜨린다. 수정(PUT)은 여전히 관리자만.)
@router.get("", response_model=ImageSettingMap)
async def get_image_settings(db: DbSession) -> ImageSettingMap:
    return await ImageSettingService(db).get_map()


@router.put("", response_model=ImageSettingMap)
async def update_image_settings(
    payload: ImageSettingMap, db: DbSession, admin: CurrentAdmin
) -> ImageSettingMap:
    return await ImageSettingService(db).update_map(payload, actor_pk=admin.pk)
