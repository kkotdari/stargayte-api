from functools import lru_cache

from app.core.config import settings
from app.storage.base import FileStorage
from app.storage.local import LocalFileStorage

__all__ = ["FileStorage", "get_storage"]


@lru_cache
def get_storage() -> FileStorage:
    if settings.storage_backend == "local":
        return LocalFileStorage()
    raise ValueError(f"지원하지 않는 storage backend 입니다: {settings.storage_backend}")
