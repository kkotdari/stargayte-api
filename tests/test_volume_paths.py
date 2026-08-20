"""저장 자리는 볼륨 안이어야 한다 — 상대경로면 재배포 때 통째로 날아간다.

한 번 겪은 일이라 시험으로 못 박는다. Railway의 컨테이너는 배포마다 새로 만들어지므로
`var/uploads` 같은 상대경로에 쌓인 것은 배포 한 번에 사라진다. 볼륨(`/data`)에 있어야
살아남는다.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings


def test_볼륨이_붙어_있으면_그_안으로_옮긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "/data")
    monkeypatch.delenv("STORAGE_LOCAL_ROOT", raising=False)
    monkeypatch.delenv("OPENBW_DATA_ROOT", raising=False)
    s = Settings()
    assert s.storage_local_root == "/data/uploads"
    assert s.openbw_data_root == "/data/bwdata"


def test_직접_정한_절대경로가_이긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """사람이 콕 집어 정한 값을 자동 규칙이 덮으면 안 된다."""
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "/data")
    monkeypatch.setenv("STORAGE_LOCAL_ROOT", "/mnt/따로/uploads")
    assert Settings().storage_local_root == "/mnt/따로/uploads"


def test_볼륨이_없으면_아무_일도_안_한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """개발 노트북·시험에서는 종전 그대로여야 한다."""
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    monkeypatch.delenv("STORAGE_LOCAL_ROOT", raising=False)
    monkeypatch.delenv("OPENBW_DATA_ROOT", raising=False)
    s = Settings()
    assert s.storage_local_root == "var/uploads"
    assert s.openbw_data_root == "var/bwdata"
