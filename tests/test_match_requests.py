"""대결 요청 코너 — @태그 폐지, 언급(표시+알림)만 유지. 자유 텍스트 + 언급 인원 0명 이상,
추천 토글, 추천순→먼저등록순 정렬/페이징(5개), 언급 알림 인박스(읽음 저장), 작성자/운영자
성사됨 완료 처리 스모크 테스트."""


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
    res = await client.patch(
        f"/api/members/{member_id}/status",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "active"},
    )
    assert res.status_code == 200, res.text


async def test_create_freetext_with_mentions_and_reject_empty(client):
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")

    # 자유 텍스트 + 언급(제한 없음). 자기 자신 언급은 무시된다.
    ok = await client.post(
        "/api/match-requests", headers=_h(a),
        json={"text": "팍규 대 Rex 보고싶어요!", "targetMemberIds": ["bob", "alice"]},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["text"] == "팍규 대 Rex 보고싶어요!"
    assert {t["memberId"] for t in body["targets"]} == {"bob"}
    assert body["mine"] is True

    # 언급 0명도 허용.
    ok2 = await client.post(
        "/api/match-requests", headers=_h(a), json={"text": "아무나 붙어요"}
    )
    assert ok2.status_code == 200, ok2.text
    assert ok2.json()["targets"] == []

    # 빈 텍스트는 거절.
    bad = await client.post("/api/match-requests", headers=_h(a), json={"text": "   "})
    assert bad.status_code in (400, 422), bad.text


async def test_inbox_notifies_mentioned_and_marks_read(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    c = await _signup(client, "carol", "Carol#1003")
    await _approve(client, a["accessToken"], "bob")
    await _approve(client, a["accessToken"], "carol")

    await client.post(
        "/api/match-requests", headers=_h(a),
        json={"text": "bob 대 carol!", "targetMemberIds": ["bob", "carol"]},
    )

    # 언급된 bob 인박스에 뜬다.
    inbox_b = (await client.get("/api/match-requests/inbox", headers=_h(b))).json()
    assert len(inbox_b["items"]) == 1
    assert inbox_b["items"][0]["text"] == "bob 대 carol!"
    assert {m["memberId"] for m in inbox_b["items"][0]["mentioned"]} == {"bob", "carol"}

    # 언급 안 된 작성자 alice 인박스는 비어있다.
    inbox_a = (await client.get("/api/match-requests/inbox", headers=_h(a))).json()
    assert inbox_a["items"] == []

    # bob이 읽음 처리하면 다시 안 뜬다. carol은 여전히 안 읽음.
    r = await client.post("/api/match-requests/inbox/read", headers=_h(b))
    assert r.status_code == 200, r.text
    assert (await client.get("/api/match-requests/inbox", headers=_h(b))).json()["items"] == []
    assert len((await client.get("/api/match-requests/inbox", headers=_h(c))).json()["items"]) == 1


async def test_recommend_sort_and_complete(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")

    r1 = await client.post("/api/match-requests", headers=_h(a), json={"text": "첫 요청"})
    r2 = await client.post("/api/match-requests", headers=_h(a), json={"text": "둘째 요청"})
    id1, id2 = r1.json()["id"], r2.json()["id"]

    rec = await client.post(f"/api/match-requests/{id2}/recommend", headers=_h(b))
    assert rec.json()["recommendCount"] == 1
    items = (await client.get("/api/match-requests", headers=_h(b))).json()["items"]
    assert [it["id"] for it in items] == [id2, id1], "추천순→먼저등록순"

    # 작성자가 아니고 운영자도 아니면 완료 처리 불가.
    bad = await client.delete(f"/api/match-requests/{id1}", headers=_h(b))
    assert bad.status_code in (400, 403), bad.text

    # 작성자(alice)는 성사됨 완료 처리 가능 → 목록에서 사라진다.
    ok = await client.delete(f"/api/match-requests/{id1}", headers=_h(a))
    assert ok.status_code == 200, ok.text
    left = (await client.get("/api/match-requests", headers=_h(b))).json()
    assert [it["id"] for it in left["items"]] == [id2]


async def test_pagination_five_per_page(client):
    a = await _signup(client, "alice", "Alice#1001")
    for i in range(7):
        res = await client.post(
            "/api/match-requests", headers=_h(a), json={"text": f"요청 {i}"}
        )
        assert res.status_code == 200, res.text
    p0 = (await client.get("/api/match-requests?page=0", headers=_h(a))).json()
    assert len(p0["items"]) == 5
    assert p0["total"] == 7
    assert p0["hasMore"] is True
    p1 = (await client.get("/api/match-requests?page=1", headers=_h(a))).json()
    assert len(p1["items"]) == 2
    assert p1["hasMore"] is False
