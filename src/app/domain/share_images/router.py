import uuid

from fastapi import APIRouter

from app.api.deps import CurrentMember, StorageDep
from app.core.exceptions import ValidationError
from app.domain.share_images.schemas import ShareImageUploadIn, ShareImageUploadOut
from app.storage.data_url import decode_data_url, guess_extension, is_data_url

router = APIRouter(prefix="/share-images", tags=["share-images"])


# 카카오톡 공유 카드 미리보기 이미지 — 랭킹 차트처럼 "그 순간의 필터/순위"를 반영해
# 프론트가 캔버스로 그린 이미지를 업로드하고 카카오가 읽어갈 수 있는 공개 URL을
# 돌려받는다(요청: "카톡 미리보기에서 차트가 보이면 좋겠어"). 회원 프로필 같은
# 영속 데이터가 아니라 매번 새로 그려지는 일회성 이미지라, 별도 DB 테이블 없이
# 스토리지에만 저장한다(아바타 업로드가 data URL을 그대로 받는 것과 같은 방식 —
# app.storage.data_url 재사용).
@router.post("", response_model=ShareImageUploadOut)
async def upload_share_image(
    payload: ShareImageUploadIn, storage: StorageDep, _current: CurrentMember,
) -> ShareImageUploadOut:
    if not is_data_url(payload.data_url):
        raise ValidationError("유효한 이미지 데이터가 아닙니다.")
    content, content_type = decode_data_url(payload.data_url)
    if not content_type.startswith("image/"):
        raise ValidationError("이미지 파일만 업로드할 수 있습니다.")
    if len(content) > 2 * 1024 * 1024:
        raise ValidationError("이미지가 너무 큽니다(2MB 이하).")
    ext = guess_extension(content_type)
    stored = await storage.save(
        subdir="share", filename=f"{uuid.uuid4().hex}{ext}", content=content, content_type=content_type,
    )
    return ShareImageUploadOut(url=stored.url)
