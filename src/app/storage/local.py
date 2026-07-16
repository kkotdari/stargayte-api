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

    async def list_files(self, subdir: str) -> list[tuple[str, int]]:
        """subdir 아래 모든 파일의 (저장 경로 key, 바이트 크기) 목록 — 리플레이 재연결
        복구 도구 전용(요청: replays 테이블 마이그레이션으로 끊긴 기존 파일 재연결).
        로컬 스토리지에서만 쓰는 헬퍼라 FileStorage 추상 인터페이스엔 없다."""
        def _scan() -> list[tuple[str, int]]:
            base = self.root / subdir
            if not base.is_dir():
                return []
            out: list[tuple[str, int]] = []
            for p in base.rglob("*"):
                if p.is_file():
                    out.append((str(p.relative_to(self.root)), p.stat().st_size))
            return out
        return await asyncio.to_thread(_scan)

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
