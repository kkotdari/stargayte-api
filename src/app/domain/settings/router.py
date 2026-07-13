from fastapi import APIRouter

from app.api.deps import CurrentAdmin, DbSession
from app.domain.settings.schemas import ImageSettingMap
from app.domain.settings.service import ImageSettingService

router = APIRouter(prefix="/settings/image-settings", tags=["settings"])


# 로그인 화면에 클럽 로고(home_logo)를 보여주려면 로그인 전에도 조회할 수 있어야 한다 —
# 민감정보가 아닌 순수 브랜딩 이미지/텍스트라 공개 엔드포인트로 둬도 안전하다. 수정(PUT)은
# 여전히 관리자만.
@router.get("", response_model=ImageSettingMap)
async def get_image_settings(db: DbSession) -> ImageSettingMap:
    return await ImageSettingService(db).get_map()


@router.put("", response_model=ImageSettingMap)
async def update_image_settings(
    payload: ImageSettingMap, db: DbSession, admin: CurrentAdmin
) -> ImageSettingMap:
    return await ImageSettingService(db).update_map(payload, actor_pk=admin.pk)
