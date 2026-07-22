"""경기 메모(메모) — 게시판 메모처럼 (작성자, 본문 최대 50자) 여러 건을 쌓고, 본문에 @닉네임
언급을 저장하며, 작성자 본인/운영자만 수정·삭제한다. 메모은 경기 목록/상세 응답에 함께 실린다.
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


async def test_create_edit_delete_note_flow(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")
    match = await _register_match(client, _h(a))
    mid = match["id"]

    # 갓 등록한 경기엔 메모이 없다.
    assert match["notes"] == []

    # 메모 작성(언급 포함).
    created = await client.post(
        f"/api/matches/{mid}/notes",
        headers=_h(b),
        json={"text": "@alice 굿 게임이었어요", "targetMemberIds": ["alice"]},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["text"] == "@alice 굿 게임이었어요"
    assert body["matchId"] == mid
    assert body["author"]["memberId"] == "bob"
    assert [m["memberId"] for m in body["mentions"]] == ["alice"]
    assert body["canEdit"] is True
    note_id = body["id"]

    # 목록 응답에 메모이 함께 실린다.
    listed = await client.get("/api/matches", headers=_h(a))
    assert listed.status_code == 200
    m0 = next(m for m in listed.json()["items"] if m["id"] == mid)
    assert len(m0["notes"]) == 1
    # 작성자(bob)가 아닌 alice 입장에서 canEdit은 False(운영자도 아님 — alice는 첫 가입자라
    # 운영자다! 그래서 canEdit True). alice는 첫 회원이라 운영자(0202)이므로 남의 메모도 수정 가능.
    assert m0["notes"][0]["canEdit"] is True

    # 메모 수정(작성자 본인) — 언급 교체.
    edited = await client.patch(
        f"/api/matches/{mid}/notes/{note_id}",
        headers=_h(b),
        json={"text": "수정된 내용", "targetMemberIds": []},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["text"] == "수정된 내용"
    assert edited.json()["mentions"] == []

    # 메모 삭제(작성자 본인).
    deleted = await client.delete(f"/api/matches/{mid}/notes/{note_id}", headers=_h(b))
    assert deleted.status_code == 204, deleted.text

    after = await client.get(f"/api/matches/{mid}", headers=_h(a))
    assert after.json()["notes"] == []


async def test_note_permissions_and_limits(client):
    a = await _signup(client, "alice", "Alice#1001")  # 첫 가입자 = 운영자
    b = await _signup(client, "bob", "Bob#1002")
    c = await _signup(client, "carol", "Carol#1003")
    await _approve(client, a["accessToken"], "bob")
    await _approve(client, a["accessToken"], "carol")
    match = await _register_match(client, _h(a))
    mid = match["id"]

    created = await client.post(
        f"/api/matches/{mid}/notes", headers=_h(b), json={"text": "밥 메모"}
    )
    cid = created.json()["id"]

    # 제3자(carol, 운영자 아님)는 남의 메모을 수정/삭제할 수 없다.
    assert created.json()["canEdit"] is True  # 작성자 본인 응답
    forbid = await client.patch(
        f"/api/matches/{mid}/notes/{cid}", headers=_h(c), json={"text": "몰래수정"}
    )
    assert forbid.status_code == 403, forbid.text
    forbid_del = await client.delete(f"/api/matches/{mid}/notes/{cid}", headers=_h(c))
    assert forbid_del.status_code == 403

    # 운영자(alice)는 남의 메모도 삭제할 수 있다.
    admin_del = await client.delete(f"/api/matches/{mid}/notes/{cid}", headers=_h(a))
    assert admin_del.status_code == 204

    # 50자 초과는 거절.
    too_long = await client.post(
        f"/api/matches/{mid}/notes", headers=_h(b), json={"text": "가" * 51}
    )
    assert too_long.status_code == 422, too_long.text

    # 빈 본문 거절.
    empty = await client.post(
        f"/api/matches/{mid}/notes", headers=_h(b), json={"text": "   "}
    )
    assert empty.status_code in (400, 422)
