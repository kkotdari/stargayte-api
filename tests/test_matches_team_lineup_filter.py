"""GET /api/matches?teamMemberIds=... — 팀 랭킹에서 팀 하나를 눌렀을 때 그 팀이 실제로
"같은 편"으로 뛴 경기만 나오는지 검증한다. 단순히 "전원이 참가한 경기"로 찾으면 서로
상대편이었던 경기까지 딸려오는데, 그건 그 팀의 전적이 아니다.
"""


async def _signup(client, member_id: str, battletag: str) -> dict:
    res = await client.post(
        "/api/auth/signup",
        json={
            "id": member_id, "password": "pass1234", "battletag": battletag,
            "replayAliases": [member_id], "insta": "",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


async def _signup_many(client, count: int) -> dict:
    first = None
    for i in range(1, count + 1):
        res = await _signup(client, f"player{i:02d}", f"Tag{i:02d}#100{i}")
        first = first or res
    return {"Authorization": f"Bearer {first['accessToken']}"}


async def _match(client, headers, team1, team2, when: str) -> str:
    def slots(ids):
        return [{"memberId": i, "race": "테란"} for i in ids]

    res = await client.post(
        "/api/matches",
        headers=headers,
        json={
            "date": when, "team1": slots(team1), "team2": slots(team2),
            "result": "team1", "note": "", "matchType": "0102" if len(team1) > 1 else "0101",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["matchNo"]


async def _list(client, headers, team_member_ids: str) -> dict:
    res = await client.get("/api/matches", headers=headers, params={"teamMemberIds": team_member_ids})
    assert res.status_code == 200, res.text
    return res.json()


async def test_team_filter_keeps_only_matches_played_on_the_same_side(client):
    headers = await _signup_many(client, 4)
    together = await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "2026-07-01")
    # 같은 네 명이 뛰었지만 p1과 p2는 서로 상대편 — 이 경기는 팀 [p1,p2]의 전적이 아니다.
    await _match(client, headers, ["player01", "player03"], ["player02", "player04"], "2026-07-02")

    body = await _list(client, headers, "player01,player02")
    assert [m["matchNo"] for m in body["items"]] == [together]
    assert body["total"] == 1


async def test_team_filter_ignores_member_order(client):
    headers = await _signup_many(client, 4)
    together = await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "2026-07-01")

    # 어느 쪽 순서로 넘겨도 같은 팀이다.
    for ids in ("player01,player02", "player02,player01"):
        body = await _list(client, headers, ids)
        assert [m["matchNo"] for m in body["items"]] == [together]


async def test_team_filter_matches_the_losing_side_too(client):
    """진 팀도 그 팀 구성으로 뛴 경기다 — 이긴 편만 걸리면 안 된다."""
    headers = await _signup_many(client, 4)
    together = await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "2026-07-01")

    body = await _list(client, headers, "player03,player04")
    assert [m["matchNo"] for m in body["items"]] == [together]


async def test_team_filter_requires_every_member(client):
    """부분집합으로는 안 걸린다 — [p1,p2,p3]로 물으면 셋이 다 같은 편이었던 경기만."""
    headers = await _signup_many(client, 6)
    duo = await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "2026-07-01")
    trio = await _match(
        client, headers, ["player01", "player02", "player05"], ["player03", "player04", "player06"], "2026-07-02",
    )

    body = await _list(client, headers, "player01,player02,player05")
    assert [m["matchNo"] for m in body["items"]] == [trio]

    # 팀 구성원은 정확히 일치해야 한다 — [p1,p2]는 2:2(duo)에서는 정확히 그 둘뿐인 편이었지만
    # 3:3(trio)에서는 p5가 낀 3인 편이었으므로, [p1,p2]로 물으면 duo만 걸리고 trio는 빠진다.
    body = await _list(client, headers, "player01,player02")
    assert [m["matchNo"] for m in body["items"]] == [duo]


async def test_single_member_filter_returns_matches_of_any_side_size(client):
    """한 명만 넘기면 편 인원수와 무관하게 그 회원이 참가한 경기 전체가 나온다 — 개인 랭킹
    상세가 그 회원의 팀경기 이력을 부를 때 2:2·3:3가 통째로 빠지던 버그 방지. 개인전/팀전
    구분은 편 인원수 조건이 아니라 matchType 필터가 맡는다."""
    headers = await _signup_many(client, 6)
    solo = await _match(client, headers, ["player01"], ["player02"], "2026-07-01")  # 0101
    duo = await _match(client, headers, ["player01", "player03"], ["player04", "player05"], "2026-07-02")  # 0102
    trio = await _match(
        client, headers, ["player01", "player03", "player05"], ["player02", "player04", "player06"], "2026-07-03",
    )  # 0102

    # matchType 없이 한 명 → 참가한 경기 전부.
    body = await _list(client, headers, "player01")
    assert {m["matchNo"] for m in body["items"]} == {solo, duo, trio}

    # 팀전만(0102) → 2:2·3:3 둘.
    team = await client.get(
        "/api/matches", headers=headers, params={"teamMemberIds": "player01", "matchType": "0102"}
    )
    assert {m["matchNo"] for m in team.json()["items"]} == {duo, trio}

    # 개인전만(0101) → 1:1 하나.
    one = await client.get(
        "/api/matches", headers=headers, params={"teamMemberIds": "player01", "matchType": "0101"}
    )
    assert [m["matchNo"] for m in one.json()["items"]] == [solo]


async def test_team_filter_with_unknown_member_returns_nothing(client):
    headers = await _signup_many(client, 4)
    await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "2026-07-01")

    body = await _list(client, headers, "player01,nosuchmember")
    assert body["items"] == []
    assert body["total"] == 0
