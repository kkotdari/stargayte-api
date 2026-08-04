"""랭크 변동을 꺼 두었을 때(요청: 지금 구조가 깔끔하지 않아 일단 기능을 멈춘다).

스위치 하나(RANKING_SHIFT_ENABLED)로 네 군데가 함께 멈춘다 — 부팅 기준선, 집계 스케줄러,
활동 카드, 제어판의 수동 버튼. 여기서는 서버가 내보내는 두 가지(카드와 손잡이)를 본다.
나머지 테스트는 conftest가 이 값을 켜 두므로 기계 자체는 계속 검사된다.

저장된 행을 지우지 않는다는 것도 함께 못 박는다 — 끄는 것과 지우는 것은 다른 일이고,
지우는 쪽은 사람이 판단할 몫이다(요청: 이미 등록된 데이터는 직접 지운다).
"""

import pytest

from app.core.config import settings

from tests.test_activity_comments import (
    _approve,
    _h,
    _recompute,
    _register_match_today,
    _signup,
)


@pytest.fixture
def ranking_shift_off():
    """이 테스트 동안만 기능을 끈다 — conftest가 켜 둔 값을 되돌려 놓는다."""
    before = settings.ranking_shift_enabled
    settings.ranking_shift_enabled = False
    try:
        yield
    finally:
        settings.ranking_shift_enabled = before


async def _snapshot_count() -> int:
    from sqlalchemy import func, select

    from app.db.session import AsyncSessionLocal
    from app.domain.activity.models import RankingShift

    async with AsyncSessionLocal() as session:
        return await session.scalar(select(func.count()).select_from(RankingShift)) or 0


async def test_disabled_hides_cards_but_keeps_rows(client, ranking_shift_off):
    """이미 쌓인 스냅샷이 있어도 카드로는 안 나간다 — 행은 그대로 남는다."""
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")

    # 켜 둔 상태로 변동을 하나 만들어 둔다(기준선 + 그 뒤 경기).
    settings.ranking_shift_enabled = True
    await _register_match_today(client, _h(a))
    await _recompute(client)
    await _register_match_today(client, _h(a), result="team2")
    await _register_match_today(client, _h(a), result="team2")
    await _recompute(client)
    assert (await client.get("/api/activities/ranking-shifts", headers=_h(a))).json() != []
    rows_before = await _snapshot_count()
    assert rows_before > 0

    # 이제 끄면 — 카드는 사라지고 행은 그대로다.
    settings.ranking_shift_enabled = False
    assert (await client.get("/api/activities/ranking-shifts", headers=_h(a))).json() == []
    assert await _snapshot_count() == rows_before

    # 활동 목록에도 랭크 변동 줄이 섞이지 않는다.
    feed = (await client.get("/api/activities", headers=_h(a))).json()
    assert all(item.get("rankingShift") is None for item in feed["items"])


async def test_disabled_blocks_admin_buttons(client, ranking_shift_off):
    """제어판의 수동 집계·기준선은 409로 막힌다 — 조용히 성공한 척하지 않는다."""
    a = await _signup(client, "alice", "Alice#1001")

    for path in ("/api/activities/ranking-shifts/recompute", "/api/activities/ranking-shifts/seed"):
        res = await client.post(path, headers=_h(a))
        assert res.status_code == 409, f"{path}: {res.status_code} {res.text}"
        assert "RANKING_SHIFT_ENABLED" in res.text

    # 막았으니 아무 행도 안 남았다.
    assert await _snapshot_count() == 0


async def test_disabled_scheduler_returns_immediately(ranking_shift_off):
    """스케줄러는 루프를 돌지 않고 바로 끝난다 — 끝나지 않으면 여기서 멈춰 선다."""
    import asyncio

    from app.main import _ranking_shift_scheduler

    await asyncio.wait_for(_ranking_shift_scheduler(), timeout=5)


async def test_disabled_boot_does_not_seed(client, ranking_shift_off):
    """부팅 기준선도 안 깐다 — 손으로 비운 표가 다음 부팅에 다시 차면 끈 의미가 없다."""
    from app.main import _seed_ranking_shifts

    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")
    await _register_match_today(client, _h(a))

    await _seed_ranking_shifts()
    assert await _snapshot_count() == 0
