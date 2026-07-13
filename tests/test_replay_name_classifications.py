"""리플레이 이름 분류(컴퓨터/비회원 기억) 조회/등록 스모크 테스트."""


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


async def test_lookup_returns_empty_for_unknown_names(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    res = await client.post(
        "/api/matches/replay-name-classifications/lookup",
        headers=headers,
        json={"rawNames": ["NoSuchPlayer"]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["classifications"] == []


async def test_set_then_lookup_roundtrips_and_upserts(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    res = await client.post(
        "/api/matches/replay-name-classifications",
        headers=headers,
        json={"rawName": "BotFriend", "kind": "computer"},
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"rawName": "BotFriend", "kind": "computer"}

    res = await client.post(
        "/api/matches/replay-name-classifications/lookup",
        headers=headers,
        json={"rawNames": ["BotFriend", "NoSuchPlayer"]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["classifications"] == [{"rawName": "BotFriend", "kind": "computer"}]

    # 같은 이름을 다른 종류로 다시 지정하면(사람이 잘못 눌렀다가 고치는 경우) 새 행이 아니라
    # 기존 행을 덮어써야 한다(raw_name UNIQUE 제약과 일치).
    res = await client.post(
        "/api/matches/replay-name-classifications",
        headers=headers,
        json={"rawName": "BotFriend", "kind": "unregistered"},
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"rawName": "BotFriend", "kind": "unregistered"}

    res = await client.post(
        "/api/matches/replay-name-classifications/lookup",
        headers=headers,
        json={"rawNames": ["BotFriend"]},
    )
    assert res.json()["classifications"] == [{"rawName": "BotFriend", "kind": "unregistered"}]


async def test_invalid_kind_rejected(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    res = await client.post(
        "/api/matches/replay-name-classifications",
        headers=headers,
        json={"rawName": "Someone", "kind": "not_a_real_kind"},
    )
    assert res.status_code == 422, res.text
