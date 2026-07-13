"""GET /api/matches의 커서 페이지네이션 + 유저 검색(OR/AND) 스모크 테스트."""


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


async def _create_match(client, headers, date: str, team1: list[str], team2: list[str]) -> dict:
    res = await client.post(
        "/api/matches",
        headers=headers,
        json={
            "date": date,
            "team1": [{"memberId": m, "race": "테란"} for m in team1],
            "team2": [{"memberId": m, "race": "저그"} for m in team2],
            "result": "team1",
            "note": "",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


async def test_pagination_orders_and_pages_through_all_pages(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    # 날짜가 겹치는 경우(같은 날짜, id로 2차 정렬)와 안 겹치는 경우를 섞는다.
    created = []
    for d in ["2026-07-01", "2026-07-01", "2026-07-02", "2026-07-03", "2026-07-03"]:
        created.append(await _create_match(client, headers, d, ["player01"], ["player02"]))

    seen_ids: list[int] = []
    cursor = None
    pages = 0
    while True:
        params = {"limit": 2, "sort": "latest"}
        if cursor:
            params["cursor"] = cursor
        res = await client.get("/api/matches", headers=headers, params=params)
        assert res.status_code == 200, res.text
        body = res.json()
        seen_ids.extend(item["id"] for item in body["items"])
        pages += 1
        if not body["hasMore"]:
            assert body["nextCursor"] is None
            break
        cursor = body["nextCursor"]
        assert pages < 10  # 무한루프 안전장치

    assert len(seen_ids) == 5
    assert len(set(seen_ids)) == 5  # 중복/누락 없음

    # latest 정렬 -> (date desc, id desc) 이어야 한다.
    dates_by_id = {m["id"]: m["date"] for m in created}
    ordered = [dates_by_id[i] for i in seen_ids]
    assert ordered == sorted(ordered, reverse=True)


async def test_cursor_is_stable_when_newer_match_inserted_concurrently(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    for d in ["2026-07-01", "2026-07-02", "2026-07-03"]:
        await _create_match(client, headers, d, ["player01"], ["player02"])

    page1 = (await client.get("/api/matches", headers=headers, params={"limit": 1, "sort": "latest"})).json()
    assert len(page1["items"]) == 1
    assert page1["items"][0]["date"] == "2026-07-03"
    cursor = page1["nextCursor"]

    # 커서를 쥔 상태에서, 그 커서보다 "더 최신"인 매치가 새로 등록된다.
    await _create_match(client, headers, "2026-07-04", ["player01"], ["player02"])

    page2_params = {"limit": 10, "sort": "latest", "cursor": cursor}
    page2 = (await client.get("/api/matches", headers=headers, params=page2_params)).json()
    page2_dates = [m["date"] for m in page2["items"]]
    # 새로 끼어든 07-04는 커서보다 앞(더 최신)이라 이 페이지에 나오면 안 되고, 이미 본
    # 07-03도 다시 나오면 안 된다 — 커서 기준 그 뒤(07-02, 07-01)만 나와야 한다.
    assert "2026-07-04" not in page2_dates
    assert "2026-07-03" not in page2_dates
    assert page2_dates == ["2026-07-02", "2026-07-01"]


async def test_user_search_or_and_and(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    await _signup(client, "player03", "Nova#1003")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    only_a = await _create_match(client, headers, "2026-07-01", ["player01"], ["player03"])
    only_b = await _create_match(client, headers, "2026-07-02", ["player02"], ["player03"])
    both_a_and_b = await _create_match(client, headers, "2026-07-03", ["player01"], ["player02"])

    # OR(기본): player01 또는 player02가 낀 경기 전부.
    or_res = await client.get(
        "/api/matches", headers=headers, params={"userQuery": "player01 player02"}
    )
    or_ids = {m["id"] for m in or_res.json()["items"]}
    assert or_ids == {only_a["id"], only_b["id"], both_a_and_b["id"]}

    # AND(matchAllUsers=true): 두 명 다 낀 경기만.
    and_res = await client.get(
        "/api/matches",
        headers=headers,
        params={"userQuery": "player01 player02", "matchAllUsers": "true"},
    )
    and_ids = {m["id"] for m in and_res.json()["items"]}
    assert and_ids == {both_a_and_b["id"]}


async def test_earliest_date_reflects_only_completed_matches(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    empty_res = await client.get("/api/matches/earliest-date", headers=headers)
    assert empty_res.json() == {"date": None}

    await _create_match(client, headers, "2026-07-05", ["player01"], ["player02"])
    await _create_match(client, headers, "2026-07-01", ["player01"], ["player02"])
    await _create_match(client, headers, "2026-07-10", ["player01"], ["player02"])

    res = await client.get("/api/matches/earliest-date", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json() == {"date": "2026-07-01"}


async def test_user_search_matches_replay_alias(client):
    # 회원가입 시 replayAlias가 로그인 id로 등록되므로, 로그인 id로 검색해도 닉네임/배틀태그가
    # 아니라 인게임 아이디(별칭) 매칭으로 찾아져야 한다.
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}
    match = await _create_match(client, headers, "2026-07-01", ["player01"], ["player02"])

    res = await client.get("/api/matches", headers=headers, params={"userQuery": "player02"})
    ids = {m["id"] for m in res.json()["items"]}
    assert ids == {match["id"]}
