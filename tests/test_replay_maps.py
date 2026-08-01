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
        # 사람이 올려 둔 실제 미니맵 그림은 아직 없다 — 그때는 격자로 그린다.
        "image": None,
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

# 여기서부터: 사람이 올려 둔 실제 미니맵 그림(요청: "미네랄 가스 물/풀/땅 벽 최대한 비슷하게").
# 타일 번호만으로 지형을 가려내는 것은 네 번 시도해 다 실패했으므로, 운영자가 맵마다 실제
# 미니맵 그림을 올려 두고 그 위에 아바타·화살표를 얹는다. 이름·판본만 다른 거의 같은 맵들이
# 한 그림을 함께 가리킬 수 있어야 한다(요청: "버전이나 이름이 다른 경우도 한데 묶을 수 있어야").
#
# 첫 가입자가 운영자가 되므로(test_smoke 참고) player01이 운영자, player02가 일반 회원이다.

# 1×1 투명 PNG.
_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGP4z8AAAAMBAQAY3Y2w"
    "AAAAAElFTkSuQmCC"
)
_MAP2 = {**_MAP, "hash": "c" * 40, "name": "빠른무한 센포금지"}


async def test_minimap_image_shared_by_similar_maps(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    # 이름만 다른 두 맵 — 격자가 한 바이트도 다르면 다른 해시라 두 행이 된다.
    await _create(client, headers, date="2026-07-05", gsa="2026-07-05T03:00:00+00:00", map_data=_MAP)
    await _create(client, headers, date="2026-07-05", gsa="2026-07-05T04:00:00+00:00", map_data=_MAP2)

    cat = await client.get("/api/game-results/replay-maps/catalog", headers=headers)
    assert cat.status_code == 200, cat.text
    body = cat.json()
    assert {m["hash"] for m in body["maps"]} == {_MAP["hash"], _MAP2["hash"]}
    # 경기 수가 함께 온다 — 어느 맵부터 그림을 올릴지 정하는 기준이다.
    assert all(m["matches"] == 1 for m in body["maps"])
    assert body["images"] == []

    made = await client.post("/api/game-results/replay-maps/images", headers=headers, json={
        "name": "빠른무한", "image": _PNG, "hashes": [_MAP["hash"], _MAP2["hash"]],
    })
    assert made.status_code == 200, made.text
    image_id = made.json()["id"]

    got = await client.get(
        "/api/game-results/replay-maps", headers=headers,
        params={"hash": [_MAP["hash"], _MAP2["hash"]]},
    )
    # 두 맵 모두 같은 그림을 돌려받는다 — 이게 '묶기'의 뜻이다.
    assert {m["image"] for m in got.json()["maps"]} == {_PNG}

    # 한쪽만 떼어 내면 그 맵은 다시 격자로 그려진다.
    off = await client.post("/api/game-results/replay-maps/assign", headers=headers, json={
        "imageId": None, "hashes": [_MAP2["hash"]],
    })
    assert off.status_code == 200, off.text
    assert off.json()["changed"] == 1
    got2 = (await client.get(
        "/api/game-results/replay-maps", headers=headers,
        params={"hash": [_MAP["hash"], _MAP2["hash"]]},
    )).json()["maps"]
    by_hash = {m["hash"]: m["image"] for m in got2}
    assert by_hash[_MAP["hash"]] == _PNG
    assert by_hash[_MAP2["hash"]] is None

    # 그림을 지우면 가리키던 맵도 함께 떨어진다.
    gone = await client.delete(f"/api/game-results/replay-maps/images/{image_id}", headers=headers)
    assert gone.status_code == 204, gone.text
    left = (await client.get(
        "/api/game-results/replay-maps", headers=headers, params={"hash": _MAP["hash"]},
    )).json()["maps"]
    assert left[0]["image"] is None


async def test_minimap_image_needs_admin(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    p2 = await _signup(client, "player02", "Mist#1002")
    # 두 번째 가입자는 승인 대기 상태라 아무 API도 못 쓴다 — 승인까지 해 둔 '일반 회원'으로
    # 만들어야 "운영자만 가능"을 확인할 수 있다.
    approve = await client.patch(
        "/api/members/player02/status",
        headers={"Authorization": f"Bearer {p1['accessToken']}"},
        json={"status": "active"},
    )
    assert approve.status_code == 200, approve.text
    headers = {"Authorization": f"Bearer {p2['accessToken']}"}
    res = await client.post("/api/game-results/replay-maps/images", headers=headers, json={
        "name": "빠른무한", "image": _PNG, "hashes": [],
    })
    assert res.status_code == 403, res.text


async def test_minimap_image_rejects_non_image(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}
    # data:image/ 로 시작하지 않는 값은 받지 않는다 — 이 문자열이 그대로 img src가 된다.
    res = await client.post("/api/game-results/replay-maps/images", headers=headers, json={
        "name": "빠른무한", "image": "javascript:alert(1)", "hashes": [],
    })
    assert res.status_code == 422, res.text


async def test_rewrite_summary_replaces_only_summary(client):
    """요약 재분석 — 규칙이 좋아지면 옛 경기도 다시 계산해 덮어쓸 수 있어야 한다(요청).

    경기 내용(팀·승패)은 그대로 두고 요약만 갈아 끼우는지, 격자가 없던 옛 경기에 미니맵이
    함께 채워지는지, 운영자만 할 수 있는지를 본다.
    """
    p1 = await _signup(client, "player01", "Shadow#1001")
    p2 = await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}
    # 격자 없이 등록된 옛 경기.
    made = await _create(client, headers, date="2026-07-01", gsa="2026-07-01T03:00:00+00:00", map_data=None)
    assert made["mapHash"] is None
    assert made["summaryData"] is None

    summary = {"v": 1, "beats": [{"k": "clash", "who": ["player01"], "won": True, "at": 100}]}
    res = await client.post(
        f"/api/game-results/{made['id']}/summary",
        headers=headers, json={"summaryData": summary, "mapData": _MAP},
    )
    assert res.status_code == 204, res.text

    got = await client.get(f"/api/game-results/{made['id']}", headers=headers)
    assert got.status_code == 200, got.text
    after = got.json()
    assert after["summaryData"] == summary
    assert after["mapHash"] == _MAP["hash"]
    # 경기 내용은 손대지 않는다.
    assert after["result"] == made["result"]
    assert [s["memberId"] for s in after["team1"]] == [s["memberId"] for s in made["team1"]]

    # 운영자가 아니면 못 한다 — 두 번째 가입자는 승인 대기라 먼저 승인해 '일반 회원'으로 만든다.
    approve = await client.patch(
        "/api/members/player02/status", headers=headers, json={"status": "active"},
    )
    assert approve.status_code == 200, approve.text
    other = {"Authorization": f"Bearer {p2['accessToken']}"}
    denied = await client.post(
        f"/api/game-results/{made['id']}/summary", headers=other, json={"summaryData": summary},
    )
    assert denied.status_code == 403, denied.text
