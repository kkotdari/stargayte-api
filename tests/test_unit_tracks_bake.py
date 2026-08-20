"""참값 트랙 굽기 — 재분석에 얹힌 자리와 '없으면 조용히 건너뛴다'를 지킨다.

굽기는 곁다리 기능이다. 덤퍼나 게임 자료가 없는 환경(개발 노트북, 시험)에서도 등록과
재분석이 그대로 돌아가야 한다 — 그게 깨지면 굽기 하나 때문에 서비스가 멈춘다.
"""

from pathlib import Path

import pytest

from app.core.config import settings
from app.domain.game_results import openbw


def test_없으면_못_굽는다고_말한다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openbw_enabled", True)
    monkeypatch.setattr(settings, "openbw_bin", str(tmp_path / "없는덤퍼"))
    monkeypatch.setattr(settings, "openbw_data_root", str(tmp_path / "없는자료"))
    assert openbw.is_available() is False
    assert "덤퍼가 없습니다" in openbw.unavailable_reason()


def test_자료만_없어도_못_굽는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_bin = tmp_path / "bwdump"
    fake_bin.write_text("")
    monkeypatch.setattr(settings, "openbw_enabled", True)
    monkeypatch.setattr(settings, "openbw_bin", str(fake_bin))
    monkeypatch.setattr(settings, "openbw_data_root", str(tmp_path / "빈자료"))
    assert openbw.is_available() is False
    assert "게임 자료가 없습니다" in openbw.unavailable_reason()


def test_꺼두면_안_굽는다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openbw_enabled", False)
    assert openbw.is_available() is False
    assert "꺼져 있습니다" in openbw.unavailable_reason()


@pytest.mark.asyncio
async def test_없는_환경에서_자동_굽기는_조용하다(monkeypatch: pytest.MonkeyPatch) -> None:
    """재분석·등록 뒤에 도는 자동 굽기는 못 구워도 던지지 않는다."""
    monkeypatch.setattr(openbw, "is_available", lambda: False)
    await openbw.bake_quietly(1)  # 예외가 안 나면 통과다


@pytest.mark.asyncio
async def test_없는_리플레이는_None(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openbw, "is_available", lambda: True)
    assert await openbw.bake(tmp_path / "없는파일.rep") is None
