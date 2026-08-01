"""접속 기록(access-ping) — 화면 코드 화이트리스트는 프론트 ScreenKey와 1:1이어야 한다.

과거엔 새 화면이 추가될 때마다 이 목록에 누락돼 그 화면 이동이 조용히 기록되지 않는
사고가 반복됐다(challenge/gameId → leagues/rivalry → 정작 홈인 feed). 지금은 없어진
화면 코드를 걷어내고 현재 화면만 남겼으므로, 그 전부가 통과하는지 여기서 못박는다.

기록된 행은 DB에서 직접 읽는다 — 접속 이력 조회 화면이 없어져 GET /auth/access-history를
지웠기 때문이다. 기록하는 쪽(access-ping)은 그대로 살아있어 검증은 계속 필요하다.
"""

from sqlalchemy import select

from app.domain.auth.models import AccessHistory

# 프론트 types/index.ts의 ScreenKey와 같은 목록.
SCREENS = ["feed", "match", "challenge", "stats", "members", "leagues", "rivalry"]


async def _history(db_session) -> list[AccessHistory]:
    """쌓인 접속 기록을 최신순으로 — 지운 조회 엔드포인트가 하던 정렬 그대로."""
    stmt = select(AccessHistory).order_by(AccessHistory.logged_in_at.desc(), AccessHistory.id.desc())
    return list((await db_session.execute(stmt)).scalars().all())


async def _signup(client, member_id: str, battletag: str) -> dict:
    res = await client.post(
        "/api/auth/signup",
        json={
            "id": member_id,
            "password": "pass1234",
            "battletag": battletag,
            "replayAliases": [member_id],
            "insta": "",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


async def test_access_ping_accepts_every_current_screen(client):
    a = await _signup(client, "alice", "Alice#1001")
    headers = {"Authorization": f"Bearer {a['accessToken']}"}

    for screen in SCREENS:
        res = await client.post(
            "/api/auth/access-ping", headers=headers, json={"screen": screen},
        )
        assert res.status_code == 204, f"{screen}: {res.text}"


async def test_access_ping_rejects_removed_screen(client):
    """없어진 화면 코드는 더 이상 받지 않는다(목록이 현재 화면과 어긋나면 여기서 잡힌다)."""
    a = await _signup(client, "alice", "Alice#1001")
    headers = {"Authorization": f"Bearer {a['accessToken']}"}

    res = await client.post(
        "/api/auth/access-ping", headers=headers, json={"screen": "ranking"},
    )
    assert res.status_code == 422, res.text


async def test_access_is_recorded_only_for_production_client(client, db_session):
    """기록 여부는 이 백엔드가 아니라 '요청을 보낸 프론트'가 운영 빌드인지로 가른다 —
    로컬 프론트가 운영 백엔드를 바라보고 개발하는 경우를 막는 게 목적이라, 백엔드의
    ENVIRONMENT로 걸면 정작 그 경우를 못 막는다(지적). 헤더가 없으면 기록한다."""
    admin = await _signup(client, "admin", "Admin#1000")
    headers = {"Authorization": f"Bearer {admin['accessToken']}"}

    async def history_len() -> int:
        return len(await _history(db_session))

    # 헤더 없음 → 기록한다(옛 빌드/외부 호출을 개발 중이라 단정할 수 없다).
    base = await history_len()
    res = await client.post("/api/auth/access-ping", headers=headers, json={"screen": "feed"})
    assert res.status_code == 204, res.text
    assert await history_len() == base + 1

    # 개발 빌드 프론트 → 남기지 않는다.
    res = await client.post(
        "/api/auth/access-ping",
        headers={**headers, "X-Client-Env": "development"},
        json={"screen": "stats"},
    )
    assert res.status_code == 204, res.text
    assert await history_len() == base + 1

    # 운영 빌드 프론트 → 남긴다.
    res = await client.post(
        "/api/auth/access-ping",
        headers={**headers, "X-Client-Env": "production"},
        json={"screen": "stats"},
    )
    assert res.status_code == 204, res.text
    assert await history_len() == base + 2


async def test_same_screen_twice_keeps_both_rows(client, db_session):
    """같은 화면을 다시 봐도 합치지 않고 한 줄씩 그대로 쌓는다(요청).

    예전엔 30분 안의 재방문을 기존 행의 시각 갱신으로 처리해서, 새로고침 한 번에 그 화면에
    처음 들어온 시각이 덮여 사라졌다."""
    admin = await _signup(client, "admin", "Admin#1000")
    headers = {"Authorization": f"Bearer {admin['accessToken']}", "X-Client-Env": "production"}

    base = len(await _history(db_session))
    for _ in range(3):
        res = await client.post("/api/auth/access-ping", headers=headers, json={"screen": "feed"})
        assert res.status_code == 204, res.text
    after = await _history(db_session)
    assert len(after) == base + 3
    assert [r.screen_code for r in after[:3]] == ["feed", "feed", "feed"]
