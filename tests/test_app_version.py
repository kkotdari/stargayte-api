"""버전 레지스트리(app_versions) — 등록된 버전 목록 조회 + 등록된 버전으로만 배포."""


async def _signup_admin(client) -> str:
    """첫 회원은 자동으로 운영자(admin)가 된다 — 그 accessToken을 돌려준다."""
    res = await client.post(
        "/api/auth/signup",
        json={
            "id": "player01", "password": "pass1234", "battletag": "Tag#1001",
            "replayAliases": ["player01"], "insta": "",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["accessToken"]


async def test_list_versions_returns_seeded_registry(client):
    token = await _signup_admin(client)
    res = await client.get("/api/app-versions", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    assert [v["number"] for v in res.json()] == ["1", "2", "3"]


async def test_set_version_to_registered_succeeds(client):
    token = await _signup_admin(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.put("/api/app-version", headers=headers, json={"activeVersion": "3"})
    assert res.status_code == 200, res.text
    assert res.json() == {"activeVersion": "3", "noticeEnabled": True}
    # 다시 조회해도 반영돼 있어야 한다.
    got = await client.get("/api/app-version", headers=headers)
    assert got.json() == {"activeVersion": "3", "noticeEnabled": True}


async def test_set_version_to_unregistered_is_rejected(client):
    token = await _signup_admin(client)
    headers = {"Authorization": f"Bearer {token}"}
    # 형식은 valid(숫자)지만 레지스트리에 없는 "9" — 400으로 막혀야 한다.
    res = await client.put("/api/app-version", headers=headers, json={"activeVersion": "9"})
    assert res.status_code == 400, res.text
    # 활성 버전은 그대로(기본 "1")여야 한다.
    got = await client.get("/api/app-version", headers=headers)
    assert got.json() == {"activeVersion": "1", "noticeEnabled": True}


async def test_set_version_rejects_bad_format(client):
    token = await _signup_admin(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.put("/api/app-version", headers=headers, json={"activeVersion": "v3"})
    assert res.status_code == 422, res.text


async def _signup_member(client, member_id: str) -> str:
    """두 번째 이후 회원은 일반 회원(비운영자) — 그 accessToken을 돌려준다."""
    res = await client.post(
        "/api/auth/signup",
        json={
            "id": member_id, "password": "pass1234", "battletag": f"Mist_{member_id}#1002",
            "replayAliases": [member_id], "insta": "",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["accessToken"]


async def test_list_versions_includes_notes(client):
    token = await _signup_admin(client)
    res = await client.get("/api/app-versions", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    # 시드된 세 버전 모두 notes 필드를 갖고, 아직 편집 전이라 빈 문자열이다.
    assert all(v["notes"] == "" for v in res.json())


async def test_admin_edits_version_notes(client):
    token = await _signup_admin(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.put(
        "/api/app-versions/3/notes", headers=headers, json={"notes": "첫째 줄\n둘째 줄"}
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"number": "3", "notes": "첫째 줄\n둘째 줄"}
    # 목록 조회에도 반영된다.
    listed = (await client.get("/api/app-versions", headers=headers)).json()
    assert next(v for v in listed if v["number"] == "3")["notes"] == "첫째 줄\n둘째 줄"


async def test_edit_notes_of_unregistered_version_is_404(client):
    token = await _signup_admin(client)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.put("/api/app-versions/9/notes", headers=headers, json={"notes": "x"})
    assert res.status_code == 404, res.text


async def test_empty_notes_clears_to_blank(client):
    token = await _signup_admin(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.put("/api/app-versions/3/notes", headers=headers, json={"notes": "내용"})
    res = await client.put("/api/app-versions/3/notes", headers=headers, json={"notes": "   "})
    assert res.status_code == 200, res.text
    assert res.json()["notes"] == ""


async def test_notice_toggle_reflected_in_status(client):
    token = await _signup_admin(client)
    headers = {"Authorization": f"Bearer {token}"}
    # 기본은 켜짐.
    assert (await client.get("/api/app-version", headers=headers)).json()["noticeEnabled"] is True
    # 끄면 상태에 반영된다.
    off = await client.put("/api/app-versions/notice-settings", headers=headers, json={"enabled": False})
    assert off.status_code == 200, off.text
    assert off.json() == {"enabled": False}
    assert (await client.get("/api/app-version", headers=headers)).json()["noticeEnabled"] is False
    # 다시 켜면 원래대로.
    await client.put("/api/app-versions/notice-settings", headers=headers, json={"enabled": True})
    assert (await client.get("/api/app-version", headers=headers)).json()["noticeEnabled"] is True


async def test_non_admin_cannot_edit_notes_or_toggle(client):
    await _signup_admin(client)
    member_token = await _signup_member(client, "player02")
    headers = {"Authorization": f"Bearer {member_token}"}
    # 운영자 전용 쓰기다 — 비운영자(승인 대기/일반 회원)는 접근이 막혀야 한다(401/403).
    assert (await client.put(
        "/api/app-versions/3/notes", headers=headers, json={"notes": "x"}
    )).status_code in (401, 403)
    assert (await client.put(
        "/api/app-versions/notice-settings", headers=headers, json={"enabled": False}
    )).status_code in (401, 403)
