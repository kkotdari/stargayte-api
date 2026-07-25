"""피드 댓글 — 대상(target_type, target_id)이 경기든 너 나와!든 같은 테이블/API 하나로
달리고, 작성자 본인/운영자만 수정·삭제한다.
"""


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


async def _register_match(client, headers: dict) -> dict:
    res = await client.post(
        "/api/matches",
        headers=headers,
        json={
            "date": "2026-04-01",
            "team1": [{"memberId": "alice", "race": "테란"}],
            "team2": [{"memberId": "bob", "race": "저그"}],
            "result": "team1",
            "matchType": "0101",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


async def test_feed_comment_crud_on_match(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")
    match = await _register_match(client, _h(a))
    mid = match["id"]

    # 작성(언급 포함).
    res = await client.post(
        "/api/feed/comments",
        headers=_h(a),
        json={"targetType": "match", "targetId": mid, "text": "@bob 좋은 경기!", "targetMemberIds": ["bob"]},
    )
    assert res.status_code == 201, res.text
    comment = res.json()
    assert comment["targetType"] == "match"
    assert comment["targetId"] == mid
    assert comment["author"]["memberId"] == "alice"
    assert comment["mentions"][0]["memberId"] == "bob"

    # 대상별 조회.
    res = await client.get(
        "/api/feed/comments",
        headers=_h(b),
        params={"targetType": "match", "targetId": mid},
    )
    assert res.status_code == 200, res.text
    items = res.json()
    assert len(items) == 1
    # 남의 댓글은 일반 회원이 수정할 수 없다.
    assert items[0]["canEdit"] is False

    # 작성자 아닌 회원의 수정은 거부된다.
    cid = comment["id"]
    res = await client.patch(
        f"/api/feed/comments/{cid}", headers=_h(b), json={"text": "고쳐쓰기"},
    )
    assert res.status_code == 403

    # 작성자 본인 수정.
    res = await client.patch(
        f"/api/feed/comments/{cid}", headers=_h(a), json={"text": "정정합니다"},
    )
    assert res.status_code == 200
    assert res.json()["text"] == "정정합니다"

    # 작성자 본인 삭제.
    res = await client.delete(f"/api/feed/comments/{cid}", headers=_h(a))
    assert res.status_code == 204

    res = await client.get(
        "/api/feed/comments",
        headers=_h(a),
        params={"targetType": "match", "targetId": mid},
    )
    assert res.json() == []


async def test_feed_comment_on_challenge_target(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")

    # 너 나와! 하나 생성해 그 id를 대상으로 댓글을 단다.
    res = await client.post(
        "/api/challenges",
        headers=_h(a),
        json={"targetMemberIds": ["bob"], "matchType": "0101", "message": ""},
    )
    assert res.status_code in (200, 201), res.text
    challenge_id = res.json()["id"]

    res = await client.post(
        "/api/feed/comments",
        headers=_h(b),
        json={"targetType": "challenge", "targetId": challenge_id, "text": "기대되는 매치"},
    )
    assert res.status_code == 201, res.text

    res = await client.get(
        "/api/feed/comments",
        headers=_h(a),
        params={"targetType": "challenge", "targetId": challenge_id},
    )
    assert res.status_code == 200
    assert [c["text"] for c in res.json()] == ["기대되는 매치"]
