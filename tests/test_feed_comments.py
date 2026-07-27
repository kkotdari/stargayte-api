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


async def test_feed_comment_edit_keeps_same_mention(client):
    """언급을 그대로 유지한 채 수정해도 UNIQUE 제약 충돌로 500이 나면 안 된다(버그 회귀).

    예전엔 한 flush에서 멘션을 통째로 재할당해 같은 (comment_id, member_pk)를
    지우기 전에 다시 INSERT하다 UNIQUE 제약에 걸렸다.
    """
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    c = await _signup(client, "choi", "Choi#1003")
    await _approve(client, a["accessToken"], "bob")
    await _approve(client, a["accessToken"], "choi")
    match = await _register_match(client, _h(a))
    mid = match["id"]

    res = await client.post(
        "/api/feed/comments",
        headers=_h(a),
        json={"targetType": "match", "targetId": mid, "text": "@bob 좋은 경기!", "targetMemberIds": ["bob"]},
    )
    assert res.status_code == 201, res.text
    cid = res.json()["id"]

    # 같은 언급 유지 — 예전 버그 재현 지점.
    res = await client.patch(
        f"/api/feed/comments/{cid}",
        headers=_h(a),
        json={"text": "@bob 수정했어요", "targetMemberIds": ["bob"]},
    )
    assert res.status_code == 200, res.text
    assert [m["memberId"] for m in res.json()["mentions"]] == ["bob"]

    # 언급 제거.
    res = await client.patch(
        f"/api/feed/comments/{cid}",
        headers=_h(a),
        json={"text": "언급 없앰", "targetMemberIds": []},
    )
    assert res.status_code == 200, res.text
    assert res.json()["mentions"] == []

    # 다른 유저로 언급 교체.
    res = await client.patch(
        f"/api/feed/comments/{cid}",
        headers=_h(a),
        json={"text": "@choi 로 바꿈", "targetMemberIds": ["choi"]},
    )
    assert res.status_code == 200, res.text
    assert [m["memberId"] for m in res.json()["mentions"]] == ["choi"]


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


async def _register_match_today(client, headers: dict, *, result: str = "team1") -> dict:
    """스냅샷은 '이번 달(KST)' 성적으로 계산되므로 오늘 날짜로 등록한다."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    res = await client.post(
        "/api/matches",
        headers=headers,
        json={
            "date": today,
            "team1": [{"memberId": "alice", "race": "테란"}],
            "team2": [{"memberId": "bob", "race": "저그"}],
            "result": result,
            "matchType": "0101",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


async def test_rank_snapshot_on_register_and_batch_merge(client):
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")

    # 첫 등록 — 두 명 모두 신규 진입 변동이 스냅샷으로 남는다(서버가 자동 계산·저장).
    m1 = await _register_match_today(client, _h(a))
    res = await client.get("/api/feed/rank-snapshots", headers=_h(a))
    assert res.status_code == 200, res.text
    events = res.json()
    assert len(events) == 1
    ev = events[0]
    assert ev["matchType"] == "0101"
    assert ev["reason"] == "register"
    assert m1["id"] in ev["matchIds"]
    assert all(s["from"] is None for s in ev["shifts"])  # 전원 신규 진입
    assert [s["to"] for s in ev["shifts"]] == sorted(s["to"] for s in ev["shifts"])

    # 연속 등록(배치) — 시간창 안이라 별도 이벤트가 아니라 기존 이벤트에 합쳐진다.
    m2 = await _register_match_today(client, _h(a))
    res = await client.get("/api/feed/rank-snapshots", headers=_h(a))
    events = res.json()
    assert len(events) == 1
    assert set(events[0]["matchIds"]) >= {m1["id"], m2["id"]}

    # 삭제 훅도 본 작업을 막지 않고 정상 동작한다(운영자 삭제).
    res = await client.delete(f"/api/matches/{m2['id']}", headers=_h(a))
    assert res.status_code == 204, res.text
    res = await client.get("/api/feed/rank-snapshots", headers=_h(a))
    assert res.status_code == 200


async def test_feed_comment_on_rankshift_target(client):
    """순위변동 알림 카드에도 같은 댓글 API가 그대로 붙는다(요청)."""
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")

    await _register_match_today(client, _h(a))
    res = await client.get("/api/feed/rank-snapshots", headers=_h(a))
    assert res.status_code == 200, res.text
    snap_id = res.json()[0]["id"]

    res = await client.post(
        "/api/feed/comments",
        headers=_h(a),
        json={
            "targetType": "rankshift", "targetId": snap_id,
            "text": "@Bob#1002 축하!", "targetMemberIds": ["bob"],
        },
    )
    assert res.status_code == 201, res.text
    created = res.json()
    assert created["targetType"] == "rankshift"

    res = await client.get(
        f"/api/feed/comments?targetType=rankshift&targetId={snap_id}", headers=_h(a)
    )
    assert res.status_code == 200, res.text
    listed = res.json()
    assert [c["id"] for c in listed] == [created["id"]]
    assert listed[0]["mentions"][0]["memberId"] == "bob"


async def test_challenge_delete_admin_only(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges",
        headers=_h(a),
        json={"targetMemberIds": ["bob"], "matchType": "0101", "message": ""},
    )
    cid = res.json()["id"]

    # 일반 회원은 삭제 불가.
    res = await client.delete(f"/api/challenges/{cid}", headers=_h(b))
    assert res.status_code == 403

    # 운영자(첫 가입자)는 삭제 가능 — 달린 피드 댓글도 함께 사라진다.
    await client.post(
        "/api/feed/comments",
        headers=_h(b),
        json={"targetType": "challenge", "targetId": cid, "text": "곧 사라질 댓글"},
    )
    res = await client.delete(f"/api/challenges/{cid}", headers=_h(a))
    assert res.status_code == 204
    res = await client.get(
        "/api/feed/comments", headers=_h(a),
        params={"targetType": "challenge", "targetId": cid},
    )
    assert res.json() == []
