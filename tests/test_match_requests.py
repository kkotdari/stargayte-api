"""대결 요청 코너 — @태그 지목(최소 2명), 추천 토글, 추천순→먼저등록순 정렬/페이징(5개),
지목된 사람만 들어주기(fulfill) 스모크 테스트."""


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


def _h(tok: dict) -> dict:
    return {"Authorization": f"Bearer {tok['accessToken']}"}


async def _approve(client, admin_token: str, member_id: str) -> None:
    # 첫 가입자(alice)가 자동으로 운영자/active가 되므로 그 토큰으로 나머지를 승인한다.
    res = await client.patch(
        f"/api/members/{member_id}/status",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "active"},
    )
    assert res.status_code == 200, res.text


async def test_create_requires_two_targets(client):
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    # 한 명만 지목하면 거절.
    res = await client.post(
        "/api/match-requests", headers=_h(a),
        json={"text": "@bob 나랑 붙자", "targetMemberIds": ["bob"]},
    )
    assert res.status_code == 422 or res.status_code == 400, res.text


async def test_full_flow_recommend_sort_and_fulfill(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    c = await _signup(client, "carol", "Carol#1003")
    await _approve(client, a["accessToken"], "bob")
    await _approve(client, a["accessToken"], "carol")

    # alice가 bob, carol을 지목해 요청 두 개 생성.
    r1 = await client.post(
        "/api/match-requests", headers=_h(a),
        json={"text": "@bob @carol 첫 요청", "targetMemberIds": ["bob", "carol"]},
    )
    assert r1.status_code == 200, r1.text
    req1 = r1.json()
    assert req1["recommendCount"] == 0
    assert {t["memberId"] for t in req1["targets"]} == {"bob", "carol"}

    r2 = await client.post(
        "/api/match-requests", headers=_h(a),
        json={"text": "@bob @carol 둘째 요청", "targetMemberIds": ["bob", "carol"]},
    )
    id1, id2 = req1["id"], r2.json()["id"]

    # 둘째 요청(id2)에 bob이 추천 → 추천 많은 순으로 id2가 위로 와야 한다.
    rec = await client.post(f"/api/match-requests/{id2}/recommend", headers=_h(b))
    assert rec.status_code == 200, rec.text
    assert rec.json()["recommendCount"] == 1
    assert rec.json()["recommendedByMe"] is True

    lst = await client.get("/api/match-requests", headers=_h(b))
    items = lst.json()["items"]
    assert [it["id"] for it in items] == [id2, id1], "추천순→먼저등록순"

    # bob은 지목됐으니 iAmTarget True, alice(작성자)는 아님.
    assert items[0]["iAmTarget"] is True
    lst_a = await client.get("/api/match-requests", headers=_h(a))
    assert lst_a.json()["items"][0]["iAmTarget"] is False
    assert lst_a.json()["items"][0]["mine"] is True

    # 추천 토글 취소.
    rec_off = await client.post(f"/api/match-requests/{id2}/recommend", headers=_h(b))
    assert rec_off.json()["recommendCount"] == 0

    # 지목 안 된 사람(작성자 alice)은 들어줄 수 없다.
    bad = await client.post(f"/api/match-requests/{id1}/fulfill", headers=_h(a))
    assert bad.status_code in (400, 403), bad.text

    # 지목된 carol은 들어줄 수 있고, 그 뒤 목록에서 사라진다.
    ok = await client.post(f"/api/match-requests/{id1}/fulfill", headers=_h(c))
    assert ok.status_code == 200, ok.text
    lst2 = await client.get("/api/match-requests", headers=_h(b))
    assert [it["id"] for it in lst2.json()["items"]] == [id2]


async def test_pagination_five_per_page(client):
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    await _signup(client, "carol", "Carol#1003")
    for i in range(7):
        res = await client.post(
            "/api/match-requests", headers=_h(a),
            json={"text": f"@bob @carol 요청 {i}", "targetMemberIds": ["bob", "carol"]},
        )
        assert res.status_code == 200, res.text
    p0 = (await client.get("/api/match-requests?page=0", headers=_h(a))).json()
    assert len(p0["items"]) == 5
    assert p0["total"] == 7
    assert p0["hasMore"] is True
    p1 = (await client.get("/api/match-requests?page=1", headers=_h(a))).json()
    assert len(p1["items"]) == 2
    assert p1["hasMore"] is False
