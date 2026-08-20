"""게임 자료 올리기 — 문이 닫혀 있고, 열려도 볼륨 밖으로는 한 줄도 안 나간다.

이 문은 저작물 파일을 볼륨에 넣는 유일한 길이라, 조용히 넓어지면 그대로 임의 파일 쓰기가
된다. 그래서 '거절해야 하는 것'을 시험으로 못 박는다.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from app.core.config import settings
from app.domain.game_results import openbw_bootstrap
from app.domain.game_results.openbw_bootstrap import BootstrapRejected


def _tgz(entries: list[tuple[str, bytes]]) -> io.BytesIO:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf


def _link_tgz(name: str, target: str) -> io.BytesIO:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name)
        info.type = tarfile.SYMTYPE
        info.linkname = target
        tar.addfile(info)
    buf.seek(0)
    return buf


def test_토큰이_없으면_문이_닫혀_있다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openbw_bootstrap_token", "")
    assert openbw_bootstrap.is_open() is False
    monkeypatch.setattr(settings, "openbw_bootstrap_token", "open-sesame")
    assert openbw_bootstrap.is_open() is True


def test_자료를_깐다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openbw_data_root", str(tmp_path / "bwdata"))
    got = openbw_bootstrap.install(_tgz([
        ("./arr/units.dat", b"\x01" * 32),
        ("./scripts/iscript.bin", b"\x02" * 16),
        ("./unit/zerg/avenger.grp", b"\x03" * 8),
    ]))
    assert got == {"files": 3, "bytes": 56}
    assert (tmp_path / "bwdata/arr/units.dat").read_bytes() == b"\x01" * 32
    assert (tmp_path / "bwdata/unit/zerg/avenger.grp").is_file()


def test_볼륨_밖을_가리키면_거절한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openbw_data_root", str(tmp_path / "bwdata"))
    with pytest.raises(BootstrapRejected):
        openbw_bootstrap.install(_tgz([("../../etc/passwd", b"bad")]))
    with pytest.raises(BootstrapRejected):
        openbw_bootstrap.install(_tgz([("/etc/passwd", b"bad")]))
    assert not (tmp_path / "bwdata").exists()


def test_심볼릭_링크는_거절한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """링크는 이름이 얌전해도 가리키는 곳이 볼륨 밖일 수 있다."""
    monkeypatch.setattr(settings, "openbw_data_root", str(tmp_path / "bwdata"))
    with pytest.raises(BootstrapRejected):
        openbw_bootstrap.install(_link_tgz("arr/units.dat", "/etc/passwd"))
    assert not (tmp_path / "bwdata").exists()


def test_한_파일이라도_어기면_아무것도_안_쓴다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """반쯤 깔린 자료는 덤퍼가 엉뚱하게 죽는 자리다 — 깔릴 거면 온전히 깔려야 한다."""
    monkeypatch.setattr(settings, "openbw_data_root", str(tmp_path / "bwdata"))
    with pytest.raises(BootstrapRejected):
        openbw_bootstrap.install(_tgz([
            ("arr/units.dat", b"\x01" * 32),
            ("arr/커다란.dat", b"\x00" * (openbw_bootstrap.MAX_FILE_BYTES + 1)),
        ]))
    assert not (tmp_path / "bwdata/arr/units.dat").exists()


def test_모르는_갈래는_조용히_버린다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """초상화·소리가 섞여 와도 거절할 일은 아니다 — 안 쓰는 것만 안 깐다."""
    monkeypatch.setattr(settings, "openbw_data_root", str(tmp_path / "bwdata"))
    got = openbw_bootstrap.install(_tgz([
        ("arr/units.dat", b"\x01" * 4),
        ("sound/zerg/죽는소리.wav", b"\x00" * 999),
        ("portrait/보기싫은얼굴.smk", b"\x00" * 999),
    ]))
    assert got["files"] == 1
    assert not (tmp_path / "bwdata/sound").exists()


def test_쓸_것이_하나도_없으면_알려_준다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """자료 폴더의 부모에서 묶으면(폴더째 들어가면) 갈래가 한 칸씩 밀린다 — 흔한 실수다."""
    monkeypatch.setattr(settings, "openbw_data_root", str(tmp_path / "bwdata"))
    with pytest.raises(BootstrapRejected, match="하나도 없습니다"):
        openbw_bootstrap.install(_tgz([("data/arr/units.dat", b"\x01" * 4)]))


def test_tar가_아니면_거절한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openbw_data_root", str(tmp_path / "bwdata"))
    with pytest.raises(BootstrapRejected, match="못 읽었습니다"):
        openbw_bootstrap.install(io.BytesIO("이건 tar가 아니다".encode()))


# ── 길 자체를 두들긴다 ────────────────────────────────────────────────────────────
# 위 시험들은 푸는 자를 직접 부른다. 아래는 토큰 검사와 본문 받기까지 통째로 지난다.


@pytest.mark.asyncio
async def test_문이_닫혀_있으면_길이_없다(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openbw_bootstrap_token", "")
    res = await client.post("/api/openbw/data", content=b"anything")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_토큰이_틀리면_있다는_것도_안_알린다(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """403이 아니라 404다 — 열려 있다는 사실 자체가 단서가 되면 안 된다."""
    monkeypatch.setattr(settings, "openbw_bootstrap_token", "real-key-1234")
    res = await client.post(
        "/api/openbw/data", content=b"x", headers={"X-Bootstrap-Token": "fake-key-1234"}
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_올리면_깔린다(client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openbw_bootstrap_token", "real-key-1234")
    monkeypatch.setattr(settings, "openbw_data_root", str(tmp_path / "bwdata"))
    body = _tgz([
        ("./arr/units.dat", b"\x01" * 32),
        ("./unit/zerg/avenger.grp", b"\x02" * 8),
    ]).getvalue()
    res = await client.post(
        "/api/openbw/data", content=body, headers={"X-Bootstrap-Token": "real-key-1234"}
    )
    assert res.status_code == 200, res.text
    got = res.json()
    assert got["files"] == 2
    # 자료는 깔렸지만 덤퍼가 없는 시험 환경이라 아직 못 굽는다 — 그 이유까지 말해 준다.
    assert got["ready"] is False
    assert got["reason"]
    assert (tmp_path / "bwdata/arr/units.dat").is_file()


@pytest.mark.asyncio
async def test_본문이_비면_보내는_법을_알려_준다(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openbw_bootstrap_token", "real-key-1234")
    res = await client.post(
        "/api/openbw/data", content=b"", headers={"X-Bootstrap-Token": "real-key-1234"}
    )
    assert res.status_code == 400
    assert "--data-binary" in res.json()["detail"]
