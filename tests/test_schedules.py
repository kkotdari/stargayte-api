"""모임 일정 — 등록·수정·삭제, 참가표시, 첨부파일, 활동 목록 편입.

너 나와!와 달리 지목한 상대도 성사 조건도 없다 — 여기서 지키는 규칙은 세 가지다:
제목과 날짜는 반드시 있어야 하고, 고치고 지우는 건 올린 사람(또는 운영자)뿐이며,
참가표시는 누구나 제 몫만 바꾼다.
"""
import base64


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


def _payload(**over) -> dict:
    base = {"title": "정기 모임", "scheduledDate": "2026-09-12", "scheduledTime": "20:00"}
    base.update(over)
    return base


async def test_create_lists_and_shows_in_activity(client):
    a = await _signup(client, "alice", "Alice#1001")

    res = await client.post("/api/schedules", headers=_h(a), json=_payload(
        content="9시부터 팀전\n늦으면 연락", linkUrl="https://example.com/map",
    ))
    assert res.status_code == 201, res.text
    made = res.json()
    assert made["title"] == "정기 모임"
    assert made["scheduledDate"] == "2026-09-12"
    assert made["scheduledTime"] == "20:00"
    assert made["content"].startswith("9시부터")
    assert made["createdBy"]["id"] == "alice"
    assert made["attendees"] == []

    res = await client.get("/api/schedules", headers=_h(a))
    assert res.status_code == 200, res.text
    assert [s["id"] for s in res.json()["items"]] == [made["id"]]

    # 활동 목록에도 제 줄로 선다 — 카드가 쓰는 내용이 그 줄에 함께 실려 온다.
    res = await client.get("/api/activities", headers=_h(a))
    assert res.status_code == 200, res.text
    rows = [it for it in res.json()["items"] if it["kind"] == "schedule"]
    assert len(rows) == 1
    assert rows[0]["key"] == f"sc-{made['id']}"
    assert rows[0]["schedule"]["title"] == "정기 모임"


async def test_title_and_date_are_required(client):
    a = await _signup(client, "alice", "Alice#1001")

    res = await client.post("/api/schedules", headers=_h(a), json={"scheduledDate": "2026-09-12"})
    assert res.status_code == 422, res.text
    res = await client.post("/api/schedules", headers=_h(a), json={"title": "모임"})
    assert res.status_code == 422, res.text
    # 제목이 공백뿐이면 안 적은 것과 같다.
    res = await client.post("/api/schedules", headers=_h(a), json=_payload(title="   "))
    assert res.status_code == 422, res.text


async def test_time_is_optional(client):
    a = await _signup(client, "alice", "Alice#1001")
    res = await client.post("/api/schedules", headers=_h(a), json={
        "title": "번개", "scheduledDate": "2026-09-12",
    })
    assert res.status_code == 201, res.text
    assert res.json()["scheduledTime"] is None


async def test_attend_set_change_and_withdraw(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")
    sid = (await client.post("/api/schedules", headers=_h(a), json=_payload())).json()["id"]

    # 남이 올린 일정에도 참가표시는 누구나 한다.
    res = await client.post(f"/api/schedules/{sid}/attend", headers=_h(b), json={"response": "going"})
    assert res.status_code == 200, res.text
    assert [(x["memberId"], x["response"]) for x in res.json()["attendees"]] == [("bob", "going")]

    # 마음이 바뀌면 같은 행이 바뀔 뿐 늘어나지 않는다.
    res = await client.post(f"/api/schedules/{sid}/attend", headers=_h(b), json={"response": "notGoing"})
    assert [(x["memberId"], x["response"]) for x in res.json()["attendees"]] == [("bob", "notGoing")]

    # 표시 자체를 거두면 '아직 답 안 함'으로 돌아간다(행이 사라진다).
    res = await client.post(f"/api/schedules/{sid}/attend", headers=_h(b), json={"response": None})
    assert res.json()["attendees"] == []


async def test_only_owner_or_admin_can_edit_and_delete(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")
    sid = (await client.post("/api/schedules", headers=_h(a), json=_payload())).json()["id"]

    res = await client.patch(f"/api/schedules/{sid}", headers=_h(b), json=_payload(title="바꿔치기"))
    assert res.status_code == 403, res.text
    res = await client.delete(f"/api/schedules/{sid}", headers=_h(b))
    assert res.status_code == 403, res.text

    # 올린 사람은 고칠 수 있다.
    res = await client.patch(f"/api/schedules/{sid}", headers=_h(a), json=_payload(
        title="정기 모임(장소 변경)", scheduledTime=None,
    ))
    assert res.status_code == 200, res.text
    assert res.json()["title"] == "정기 모임(장소 변경)"
    assert res.json()["scheduledTime"] is None

    # 첫 회원은 운영자라 남의 일정도 지울 수 있다 — 여기서는 제 것을 지운다.
    assert (await client.delete(f"/api/schedules/{sid}", headers=_h(a))).status_code == 204
    assert (await client.get("/api/schedules", headers=_h(a))).json()["items"] == []


async def test_files_are_stored_kept_and_dropped(client):
    a = await _signup(client, "alice", "Alice#1001")
    data = "data:text/plain;base64," + base64.b64encode(b"hello").decode()

    res = await client.post("/api/schedules", headers=_h(a), json=_payload(
        files=[{"name": "안내.txt", "data": data}],
    ))
    assert res.status_code == 201, res.text
    made = res.json()
    assert len(made["files"]) == 1
    assert made["files"][0]["name"] == "안내.txt"
    assert made["files"][0]["size"] == 5
    # 저장 경로(path)는 안 내보낸다 — 화면이 쓸 일이 없고 저장소 내부 열쇠다.
    assert "path" not in made["files"][0]

    # 그대로 두는 파일은 url만 다시 보낸다.
    kept = {k: made["files"][0][k] for k in ("name", "url", "size")}
    res = await client.patch(f"/api/schedules/{made['id']}", headers=_h(a), json=_payload(files=[kept]))
    assert [f["url"] for f in res.json()["files"]] == [kept["url"]]

    # 목록에서 빠지면 지운 것이다.
    res = await client.patch(f"/api/schedules/{made['id']}", headers=_h(a), json=_payload(files=[]))
    assert res.json()["files"] == []


async def test_comments_attach_to_schedule(client):
    a = await _signup(client, "alice", "Alice#1001")
    sid = (await client.post("/api/schedules", headers=_h(a), json=_payload())).json()["id"]

    res = await client.post("/api/activities/comments", headers=_h(a), json={
        "targetType": "schedule", "targetId": sid, "text": "참석합니다",
    })
    assert res.status_code == 201, res.text

    res = await client.get("/api/activities", headers=_h(a))
    row = next(it for it in res.json()["items"] if it["kind"] == "schedule")
    assert [c["text"] for c in row["comments"]] == ["참석합니다"]

    # 일정을 지우면 거기 달린 댓글도 함께 사라진다.
    assert (await client.delete(f"/api/schedules/{sid}", headers=_h(a))).status_code == 204
    res = await client.get(
        "/api/activities/comments", headers=_h(a), params={"targetType": "schedule", "targetId": sid}
    )
    assert res.json() == []
