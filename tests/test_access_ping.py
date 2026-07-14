"""접속 기록(access-ping) — 화면 코드 화이트리스트에 challenge/gameId가 빠져 있어서
그 화면으로의 이동이 조용히 기록 안 되던 문제(요청: "접속 이력 남길때 새 메뉴인
너 나와의 코드가 안들어가는거 같음")."""


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


async def test_access_ping_accepts_challenge_and_game_id_screens(client):
    a = await _signup(client, "alice", "Alice#1001")
    headers = {"Authorization": f"Bearer {a['accessToken']}"}

    for screen in ["challenge", "gameId"]:
        res = await client.post(
            "/api/auth/access-ping", headers=headers, json={"screen": screen},
        )
        assert res.status_code == 204, res.text
