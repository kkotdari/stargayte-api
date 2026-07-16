import asyncio
import uuid
from pathlib import Path

from app.core.config import settings
from app.storage.base import FileStorage, StoredFile


class LocalFileStorage(FileStorage):
    """서버 로컬 디스크에 파일을 저장하고 정적 파일로 서빙한다 (main.py 의 StaticFiles mount 참고)."""

    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or settings.storage_local_root)

    async def save(
        self, *, subdir: str, filename: str, content: bytes, content_type: str | None = None
    ) -> StoredFile:
        ext = Path(filename).suffix
        key = f"{subdir}/{uuid.uuid4().hex}{ext}"
        dest = self.root / key
        await asyncio.to_thread(self._write, dest, content)
        return StoredFile(path=key, url=self.url_for(key))

    async def delete(self, path: str) -> None:
        await asyncio.to_thread(self._delete, self.root / path)

    async def read(self, path: str) -> bytes:
        return await asyncio.to_thread((self.root / path).read_bytes)

    def url_for(self, path: str) -> str:
        url_path = f"{settings.storage_url_path.rstrip('/')}/{path}"
        return f"{settings.public_base_url.rstrip('/')}{url_path}"

    @staticmethod
    def _write(dest: Path, content: bytes) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)

    @staticmethod
    def _delete(target: Path) -> None:
        target.unlink(missing_ok=True)
