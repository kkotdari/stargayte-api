"""미니맵 격자 저장 — 같은 맵이면 한 벌만 저장하고 경기는 해시로 그걸 가리킨다(요청:
"같은 맵을 반복해서 쓰기 때문에 맵이 동일하면 똑같은 미니맵을 활용해도 될 것 같다").

여기서 확인하는 것: 등록 payload의 격자가 저장되고 응답에 mapHash가 붙는지, 같은 해시를
두 번 올려도 행이 하나뿐인지, 조회 엔드포인트가 격자를 그대로 돌려주는지, 옛 경기에
머지로 미니맵이 채워지는지, 그리고 크기가 안 맞는 격자는 거절되는지.
"""

import base64

# 4×4 격자 — 팔레트 첨자 0/1이 번갈아 든 것(실제 맵의 체크무늬와 같은 모양).
_W, _H = 4, 4
_TILES = base64.b64encode(bytes([(x + y) % 2 for y in range(_H) for x in range(_W)])).decode()
_MAP = {
    "hash": "a" * 40, "name": "빠른무한", "width": _W, "height": _H,
    "palette": [4, 17], "tiles": _TILES, "resources": [[1, 1, 1]],
}


async def _signup(client, member_id: str, battletag: str) -> dict:
    res = await client.post(
        "/api/auth/signup",
        json={"id": member_id, "password": "pass1234", "battletag": battletag,
              "replayAliases": [member_id], "insta": ""},
    )
    assert res.status_code == 200, res.text
    return res.json()


async def _create(client, headers, *, date: str, gsa: str, map_data: dict | None) -> dict:
    res = await client.post("/api/game-results", headers=headers, json={
        "date": date,
        "team1": [{"memberId": "player01", "race": "테란", "playerName": "player01"}],
        "team2": [{"memberId": "player02", "race": "저그", "playerName": "player02"}],
        "result": "team1", "gameStartedAt": gsa,
        **({"mapData": map_data} if map_data is not None else {}),
    })
    assert res.status_code == 200, res.text
    return res.json()


async def test_same_map_stored_once_and_fetchable(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    # 같은 맵으로 두 경기 — 격자는 한 벌만 저장돼야 한다.
    a = await _create(client, headers, date="2026-07-01", gsa="2026-07-01T03:00:00+00:00", map_data=_MAP)
    b = await _create(client, headers, date="2026-07-01", gsa="2026-07-01T04:00:00+00:00", map_data=_MAP)
    assert a["mapHash"] == _MAP["hash"]
    assert b["mapHash"] == _MAP["hash"]

    got = await client.get("/api/game-results/replay-maps", headers=headers, params={"hash": _MAP["hash"]})
    assert got.status_code == 200, got.text
    maps = got.json()["maps"]
    # 두 경기가 같은 해시를 가리키는데 격자는 하나뿐 — 이게 이 테이블의 존재 이유다.
    assert len(maps) == 1
    assert maps[0] == {
        "hash": _MAP["hash"], "name": "빠른무한", "width": _W, "height": _H,
        "palette": [4, 17], "tiles": _TILES, "resources": [[1.0, 1.0, 1.0]],
    }


async def test_missing_hash_is_simply_absent(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}
    got = await client.get("/api/game-results/replay-maps", headers=headers, params={"hash": "b" * 40})
    assert got.status_code == 200, got.text
    # 모르는 해시는 오류가 아니라 빈손이다 — 그 경기만 미니맵 없이 그려지면 된다.
    assert got.json()["maps"] == []


async def test_manual_registration_has_no_map(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}
    row = await _create(client, headers, date="2026-07-02", gsa="2026-07-02T03:00:00+00:00", map_data=None)
    assert row["mapHash"] is None


async def test_merge_backfills_map_for_old_match(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}
    gsa = "2026-07-03T03:00:00+00:00"
    row = await _create(client, headers, date="2026-07-03", gsa=gsa, map_data=None)
    assert row["mapHash"] is None

    merge = await client.post("/api/game-results/merge-replay", headers=headers, json={
        "gameStartedAt": gsa, "result": None, "mapData": _MAP,
        "players": [{"playerName": "player01"}, {"playerName": "player02"}],
    })
    assert merge.status_code == 200, merge.text
    assert merge.json()["merged"] is True

    got = (await client.get(f"/api/game-results/{row['id']}", headers=headers)).json()
    assert got["mapHash"] == _MAP["hash"]


async def test_grid_length_must_match_size(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}
    # 4×4라고 말하면서 격자는 한 바이트 — 잘렸거나 다른 맵이라는 뜻이라 받지 않는다.
    res = await client.post("/api/game-results", headers=headers, json={
        "date": "2026-07-04",
        "team1": [{"memberId": "player01", "race": "테란", "playerName": "player01"}],
        "team2": [{"memberId": "player02", "race": "저그", "playerName": "player02"}],
        "result": "team1", "gameStartedAt": "2026-07-04T03:00:00+00:00",
        "mapData": {**_MAP, "tiles": base64.b64encode(bytes([0])).decode()},
    })
    assert res.status_code == 422, res.text
