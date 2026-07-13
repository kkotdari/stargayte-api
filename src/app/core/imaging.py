from io import BytesIO

from PIL import Image

# frontend/src/utils/image.ts 의 resizeAvatarImage 와 동일한 정책(긴 변 기준 480px, JPEG 92%)
DEFAULT_MAX_SIDE = 480
DEFAULT_QUALITY = 92


def resize_image_bytes(
    content: bytes, *, max_side: int = DEFAULT_MAX_SIDE, quality: int = DEFAULT_QUALITY
) -> bytes:
    """이미지를 긴 변 기준 max_side 이하로 고품질 축소한 JPEG 바이트로 재인코딩한다.
    브라우저에서 canvas로 처리하던 걸 서버에서 그대로 하는 버전 — 이미 저장된 사진을
    다시 불러와 재처리할 때는 CORS 제약 없이 서버 로컬에서 처리하는 게 안전하다."""
    img = Image.open(BytesIO(content))
    img = img.convert("RGB")  # JPEG는 알파 채널을 지원하지 않는다
    width, height = img.size
    scale = min(1.0, max_side / max(width, height))
    if scale < 1.0:
        img = img.resize((round(width * scale), round(height * scale)), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()
