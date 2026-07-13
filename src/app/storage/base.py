from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class StoredFile:
    path: str  # 스토리지 내부 저장 경로/키 (삭제 시 사용)
    url: str  # 클라이언트가 접근 가능한 절대 URL


class FileStorage(ABC):
    """파일 저장소 추상 인터페이스.

    구현체를 local/S3 등으로 교체해도 도메인 서비스 코드는 변경할 필요가 없도록
    저장/삭제/조회만 노출한다.
    """

    @abstractmethod
    async def save(
        self, *, subdir: str, filename: str, content: bytes, content_type: str | None = None
    ) -> StoredFile: ...

    @abstractmethod
    async def delete(self, path: str) -> None: ...

    @abstractmethod
    async def read(self, path: str) -> bytes:
        """원본 파일명으로 다운로드시켜주기 위해(Content-Disposition) 바이트를 직접 읽는다."""
        ...

    @abstractmethod
    def url_for(self, path: str) -> str:
        """저장 경로(key)로부터 클라이언트가 접근 가능한 절대 URL을 재구성한다."""
        ...
