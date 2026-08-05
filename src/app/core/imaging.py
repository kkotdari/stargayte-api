from io import BytesIO

from PIL import Image

# frontend/src/utils/image.ts 의 resizeAvatarImage 와 동일한 정책(긴 변 기준 480px, JPEG 92%)
DEFAULT_MAX_SIDE = 480
DEFAULT_QUALITY = 92


def image_size(content: bytes) -> tuple[int, int]:
    """이미지 바이트의 (가로, 세로)를 읽는다 — 픽셀을 디코딩하지 않고 헤더만 본다.

    저장한 뒤의 실제 크기를 알아야 하는 곳이 있다: 카카오 공유 카드는 그림의 가로·세로를
    함께 넘겨야 그 비율로 앉히므로(안 주면 제 자리 비율에 맞춰 잘라 낸다), 브라우저가
    알려 준 원본 크기가 아니라 서버가 줄인 뒤의 크기를 적어 둬야 한다."""
    with Image.open(BytesIO(content)) as img:
        return img.size


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
