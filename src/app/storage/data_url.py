"""프론트엔드에서 FileReader.readAsDataURL 로 만들어 보내는 data URL(base64)을
디코딩하는 유틸리티. 아바타/경기 첨부파일 모두 이 형식으로 업로드된다."""

import base64
import re
from pathlib import Path

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[\w.+-]+/[\w.+-]+);base64,(?P<data>.+)$", re.DOTALL)

_EXTENSION_BY_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}


def is_data_url(value: str | None) -> bool:
    return bool(value) and value.startswith("data:") and ";base64," in value


def decode_data_url(data_url: str) -> tuple[bytes, str]:
    """data URL 을 (원본 바이트, content-type) 으로 디코딩한다."""
    match = _DATA_URL_RE.match(data_url)
    if not match:
        raise ValueError("유효한 data URL 형식이 아닙니다.")
    content = base64.b64decode(match.group("data"))
    return content, match.group("mime")


def guess_extension(content_type: str, fallback_filename: str = "") -> str:
    return _EXTENSION_BY_MIME.get(content_type) or Path(fallback_filename).suffix or ".bin"
