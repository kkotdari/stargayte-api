"""접속 기록(access-ping) — 화면 코드 화이트리스트는 프론트 ScreenKey와 1:1이어야 한다.

과거엔 새 화면이 추가될 때마다 이 목록에 누락돼 그 화면 이동이 조용히 기록되지 않는
사고가 반복됐다(challenge/gameId → leagues/rivalry → 정작 홈인 feed). 지금은 없어진
화면 코드를 걷어내고 현재 화면만 남겼으므로, 그 전부가 통과하는지 여기서 못박는다.
"""

# 프론트 types/index.ts의 ScreenKey와 같은 목록.
SCREENS = ["feed", "match", "challenge", "stats", "members", "leagues", "rivalry"]


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
