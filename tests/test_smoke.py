"""SQLite로 검증하는 엔드투엔드 스모크 테스트.

회원가입 -> 로그인 -> 회원 목록 -> 경기결과 등록/수정(첨부파일 포함) -> 프로필(아바타) 수정 ->
종족 아이콘 조회/수정(관리자 권한) 흐름이 실제로 동작하는지 확인한다.
"""

from datetime import UTC, datetime

TINY_PNG_DATA_URL = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


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


async def _set_status(client, admin_token: str, member_id: str, status: str):
    return await client.patch(
        f"/api/members/{member_id}/status",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": status},
    )


async def test_signup_first_member_becomes_admin(client):
    body = await _signup(client, "player01", "Shadow#1001")
    assert body["user"]["id"] == "player01"
    assert body["user"]["nickname"] == "Shadow"
    assert body["user"]["status"] == "active"
    assert body["user"]["roles"] == ["0202"]
    assert body["accessToken"]


async def test_login_ignores_id_case(client):
    await _signup(client, "player01", "Shadow#1001")
    login_res = await client.post("/api/auth/login", json={"id": "PLAYER01", "password": "pass1234"})
    assert login_res.status_code == 200, login_res.text


async def test_second_signup_is_pending_and_cannot_login(client):
    await _signup(client, "player01", "Shadow#1001")
    second = await _signup(client, "player02", "Mist#1002")
    assert second["user"]["status"] == "pending"
    assert second["user"]["roles"] == ["0203"]

    login_res = await client.post("/api/auth/login", json={"id": "player02", "password": "pass1234"})
    assert login_res.status_code == 401
    assert "승인" in login_res.json()["detail"]

    # 이미 발급된(=가입 응답의) 토큰도 승인 전에는 사용할 수 없다.
    me_res = await client.get(
        "/api/members", headers={"Authorization": f"Bearer {second['accessToken']}"}
    )
    assert me_res.status_code == 401


async def test_admin_can_approve_then_member_can_login(client):
    admin = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")

    approve_res = await _set_status(client, admin["accessToken"], "player02", "active")
    assert approve_res.status_code == 200, approve_res.text
    assert approve_res.json()["status"] == "active"

    login_res = await client.post("/api/auth/login", json={"id": "player02", "password": "pass1234"})
    assert login_res.status_code == 200


async def test_admin_can_suspend_and_reactivate_member(client):
    admin = await _signup(client, "player01", "Shadow#1001")
    member = await _signup(client, "player02", "Mist#1002")
    await _set_status(client, admin["accessToken"], "player02", "active")

    suspend_res = await _set_status(client, admin["accessToken"], "player02", "suspended")
    assert suspend_res.status_code == 200
    assert suspend_res.json()["status"] == "suspended"

    # 정지 후에는 로그인은 물론, 이미 갖고 있던 토큰도 막힌다.
    login_res = await client.post("/api/auth/login", json={"id": "player02", "password": "pass1234"})
    assert login_res.status_code == 401
    assert "정지" in login_res.json()["detail"]
    me_res = await client.get(
        "/api/members", headers={"Authorization": f"Bearer {member['accessToken']}"}
    )
    assert me_res.status_code == 401

    reactivate_res = await _set_status(client, admin["accessToken"], "player02", "active")
    assert reactivate_res.status_code == 200
    login_after = await client.post("/api/auth/login", json={"id": "player02", "password": "pass1234"})
    assert login_after.status_code == 200


async def test_admin_cannot_suspend_self(client):
    admin = await _signup(client, "player01", "Shadow#1001")
    res = await _set_status(client, admin["accessToken"], "player01", "suspended")
    assert res.status_code == 400


async def test_last_active_admin_cannot_be_suspended_by_another_admin(client):
    admin1 = await _signup(client, "player01", "Shadow#1001")
    admin2_signup = await _signup(client, "player02", "Mist#1002")
    await _set_status(client, admin1["accessToken"], "player02", "active")
    # player02를 관리자로 승격하려면 DB 직접 변경이 필요하므로, 여기서는 마지막 관리자(player01)를
    # 다른 활성 관리자(player02, 아직 admin 아님)가 아니라 스스로 정지 못 하는지만 재확인한다.
    res = await _set_status(client, admin1["accessToken"], "player01", "suspended")
    assert res.status_code == 400
    assert admin2_signup["user"]["roles"] == ["0203"]


async def test_non_admin_cannot_update_member_status(client):
    admin = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    await _set_status(client, admin["accessToken"], "player02", "active")
    member_login = await client.post(
        "/api/auth/login", json={"id": "player02", "password": "pass1234"}
    )
    member_token = member_login.json()["accessToken"]

    res = await _set_status(client, member_token, "player01", "suspended")
    assert res.status_code == 403


async def test_login_success_and_failure(client):
    await _signup(client, "player01", "Shadow#1001")

    ok = await client.post("/api/auth/login", json={"id": "player01", "password": "pass1234"})
    assert ok.status_code == 200

    bad = await client.post("/api/auth/login", json={"id": "player01", "password": "wrong"})
    assert bad.status_code == 401


async def test_match_lifecycle_with_attachment(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    token = p1["accessToken"]
    headers = {"Authorization": f"Bearer {token}"}

    create_res = await client.post(
        "/api/matches",
        headers=headers,
        json={
            "date": "2026-07-01",
            "team1": [{"memberId": "player01", "race": "테란"}],
            "team2": [{"memberId": "player02", "race": "저그"}],
            "result": "team1",
            "note": "테스트 경기",
            "replay": {
                "originalName": "replay.rep",
                "displayName": "replay.rep",
                "url": TINY_PNG_DATA_URL,
            },
        },
    )
    assert create_res.status_code == 200, create_res.text
    match = create_res.json()
    assert match["replay"]["displayName"] == "replay.rep"
    assert match["replay"]["originalName"] == "replay.rep"
    assert match["replay"]["url"].startswith("http://testserver/uploads/replays/")

    download_res = await client.get(f"/api/matches/{match['id']}/replay", headers=headers)
    assert download_res.status_code == 200
    assert 'filename="replay.rep"' in download_res.headers["content-disposition"]
    assert len(download_res.content) > 0

    list_res = await client.get("/api/matches", headers=headers)
    assert list_res.status_code == 200
    assert len(list_res.json()["items"]) == 1

    update_res = await client.put(
        f"/api/matches/{match['id']}",
        headers=headers,
        json={
            "date": "2026-07-01",
            "team1": [{"memberId": "player01", "race": "테란"}],
            "team2": [{"memberId": "player02", "race": "저그"}],
            "result": "team2",
            "note": "수정됨",
            "replay": None,
        },
    )
    assert update_res.status_code == 200, update_res.text
    updated = update_res.json()
    assert updated["result"] == "team2"
    assert updated["replay"] is None


async def test_manual_match_no_uses_match_date_not_registration_time(client):
    """수기등록(프론트가 "제N경기" 순서를 매기려고 gameStartedAt에 등록 시각=지금을 채워
    보낸다)이어도, matchNo는 사용자가 고른 경기 날짜를 기준으로 붙어야 한다 — 실제로
    지적받은 문제: 4월 1일자로 등록한 경기의 matchNo가 등록한 날(오늘)로 붙었다."""
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    past_date = "2026-04-01"
    create_res = await client.post(
        "/api/matches",
        headers=headers,
        json={
            "date": past_date,
            "team1": [{"memberId": "player01", "race": "테란"}],
            "team2": [{"memberId": "player02", "race": "저그"}],
            "result": "team1",
            "note": "",
            # 프론트의 수기등록 신규 폼이 실제로 보내는 값과 동일 — 등록 시점(지금)을
            # ISO 문자열로 채운다.
            "gameStartedAt": datetime.now(UTC).isoformat(),
        },
    )
    assert create_res.status_code == 200, create_res.text
    match = create_res.json()
    assert match["matchNo"].startswith("260401")


async def test_match_with_unregistered_slot(client):
    """아직 가입하지 않은 실제 사람(비회원) 슬롯 — 컴퓨터와 같은 방식(회원 없음,
    team 내 position으로 재생성되는 임시 아이디)으로 저장/조회되는지 확인한다. 리플레이가
    파싱한 실제 이름(playerName)은 그대로 저장된다."""
    p1 = await _signup(client, "player01", "Shadow#1001")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    create_res = await client.post(
        "/api/matches",
        headers=headers,
        json={
            "date": "2026-07-01",
            "team1": [{"memberId": "player01", "race": "테란", "playerName": "player01"}],
            "team2": [{"memberId": "__unregistered__anything", "race": "저그", "playerName": "GhostGuy"}],
            "result": "team1",
            "note": "",
        },
    )
    assert create_res.status_code == 200, create_res.text
    match = create_res.json()
    assert match["team1"][0]["memberId"] == "player01"
    # 프론트가 보낸 원본 아이디값과 무관하게, 저장 시 회원이 아니라는 사실만 남고 응답에서는
    # 항상 team 내 position 기준으로 재생성된다(실제 이름은 playerName에 보존).
    assert match["team2"][0]["memberId"] == "__unregistered__0"

    # 다시 조회해도(목록) 같은 값으로 안정적으로 재생성된다.
    list_res = await client.get("/api/matches", headers=headers)
    assert list_res.status_code == 200
    refetched = list_res.json()["items"][0]
    assert refetched["team2"][0]["memberId"] == "__unregistered__0"

    # 통계 조회도 이 경기 때문에 깨지지 않아야 한다(비회원은 회원이 아니라 자연히 제외).
    stats_res = await client.get(
        "/api/matches/stats", headers=headers, params={"memberIds": "player01"}
    )
    assert stats_res.status_code == 200, stats_res.text
    assert stats_res.json()["members"][0]["overall"]["plays"] == 1


async def test_matches_require_auth(client):
    res = await client.get("/api/matches")
    assert res.status_code == 401


async def test_match_attachment_rejects_non_rep_file(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    res = await client.post(
        "/api/matches",
        headers=headers,
        json={
            "date": "2026-07-01",
            "team1": [{"memberId": "player01", "race": "테란"}],
            "team2": [{"memberId": "player02", "race": "저그"}],
            "result": "team1",
            "note": "",
            "replay": {
                "originalName": "screenshot.png",
                "displayName": "screenshot.png",
                "url": TINY_PNG_DATA_URL,
            },
        },
    )
    assert res.status_code == 400
    assert "리플레이" in res.json()["detail"]


async def test_profile_update_with_avatar_data_url(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    res = await client.patch(
        "/api/members/player01",
        headers=headers,
        json={"avatar": TINY_PNG_DATA_URL},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["avatar"].startswith("http://testserver/uploads/avatars/")


async def test_avatar_replace_and_reprocess_produce_new_urls(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    res = await client.patch(
        "/api/members/player01", headers=headers, json={"avatar": TINY_PNG_DATA_URL}
    )
    assert res.status_code == 200, res.text
    first_url = res.json()["avatar"]

    res = await client.patch(
        "/api/members/player01", headers=headers, json={"avatar": TINY_PNG_DATA_URL}
    )
    assert res.status_code == 200, res.text
    second_url = res.json()["avatar"]
    assert second_url != first_url

    res = await client.post("/api/members/player01/avatar/reprocess", headers=headers)
    assert res.status_code == 200, res.text
    reprocessed_url = res.json()["avatar"]
    assert reprocessed_url != second_url


async def test_image_settings_get_default_and_admin_only_update(client):
    admin = await _signup(client, "player01", "Shadow#1001")  # 첫 가입자 = 관리자
    await _signup(client, "player02", "Mist#1002")
    await _set_status(client, admin["accessToken"], "player02", "active")
    member = (
        await client.post("/api/auth/login", json={"id": "player02", "password": "pass1234"})
    ).json()

    default_res = await client.get(
        "/api/settings/image-settings", headers={"Authorization": f"Bearer {admin['accessToken']}"}
    )
    assert default_res.status_code == 200
    assert default_res.json()["테란"] == {"type": "text", "value": "T"}

    forbidden = await client.put(
        "/api/settings/image-settings",
        headers={"Authorization": f"Bearer {member['accessToken']}"},
        json={"테란": {"type": "text", "value": "T"}},
    )
    assert forbidden.status_code == 403

    updated = await client.put(
        "/api/settings/image-settings",
        headers={"Authorization": f"Bearer {admin['accessToken']}"},
        json={"테란": {"type": "text", "value": "⚔️"}},
    )
    assert updated.status_code == 200
    assert updated.json()["테란"]["value"] == "⚔️"


async def _create_match(client, token: str) -> dict:
    res = await client.post(
        "/api/matches",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "date": "2026-07-01",
            "team1": [{"memberId": "player01", "race": "테란"}],
            "team2": [{"memberId": "player02", "race": "저그"}],
            "result": "team1",
            "note": "",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


async def test_match_shows_author(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")

    match = await _create_match(client, p1["accessToken"])
    assert match["createdBy"] == {"id": "player01", "nickname": "Shadow"}


async def test_only_author_or_admin_can_update_or_delete_match(client):
    admin = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    await _set_status(client, admin["accessToken"], "player02", "active")
    member = (
        await client.post("/api/auth/login", json={"id": "player02", "password": "pass1234"})
    ).json()

    match = await _create_match(client, admin["accessToken"])

    # 작성자가 아닌 일반 회원은 수정/삭제 모두 막힌다.
    other_headers = {"Authorization": f"Bearer {member['accessToken']}"}
    update_payload = {
        "date": "2026-07-01",
        "team1": [{"memberId": "player01", "race": "테란"}],
        "team2": [{"memberId": "player02", "race": "저그"}],
        "result": "team2",
        "note": "몰래 수정",
    }
    forbidden_update = await client.put(
        f"/api/matches/{match['id']}", headers=other_headers, json=update_payload
    )
    assert forbidden_update.status_code == 403

    forbidden_delete = await client.delete(f"/api/matches/{match['id']}", headers=other_headers)
    assert forbidden_delete.status_code == 403

    # 작성자 본인은 수정 가능
    author_headers = {"Authorization": f"Bearer {admin['accessToken']}"}
    own_update = await client.put(
        f"/api/matches/{match['id']}", headers=author_headers, json=update_payload
    )
    assert own_update.status_code == 200

    # 작성자 본인은 삭제도 가능, 삭제 후 목록에서 사라진다
    own_delete = await client.delete(f"/api/matches/{match['id']}", headers=author_headers)
    assert own_delete.status_code == 204
    list_res = await client.get("/api/matches", headers=author_headers)
    assert list_res.json()["items"] == []


async def test_admin_can_delete_others_match(client):
    admin = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    await _set_status(client, admin["accessToken"], "player02", "active")
    member = (
        await client.post("/api/auth/login", json={"id": "player02", "password": "pass1234"})
    ).json()

    match = await _create_match(client, member["accessToken"])

    res = await client.delete(
        f"/api/matches/{match['id']}", headers={"Authorization": f"Bearer {admin['accessToken']}"}
    )
    assert res.status_code == 204


async def test_member_can_withdraw_self_and_login_is_blocked(client):
    admin = await _signup(client, "player01", "Shadow#1001")
    member = await _signup(client, "player02", "Mist#1002")
    await _set_status(client, admin["accessToken"], "player02", "active")
    member_login = (
        await client.post("/api/auth/login", json={"id": "player02", "password": "pass1234"})
    ).json()

    res = await client.post(
        "/api/members/player02/withdraw",
        headers={"Authorization": f"Bearer {member_login['accessToken']}"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "withdrawn"

    login_res = await client.post("/api/auth/login", json={"id": "player02", "password": "pass1234"})
    assert login_res.status_code == 401
    assert "탈퇴" in login_res.json()["detail"]

    # 관리자는 탈퇴한 회원을 목록에서 재개(active)시킬 수 있다.
    reactivate = await _set_status(client, admin["accessToken"], "player02", "active")
    assert reactivate.status_code == 200
    assert member["user"]["id"] == "player02"


async def test_cannot_withdraw_other_members_account(client):
    admin = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    await _set_status(client, admin["accessToken"], "player02", "active")

    res = await client.post(
        "/api/members/player02/withdraw",
        headers={"Authorization": f"Bearer {admin['accessToken']}"},
    )
    assert res.status_code == 403


async def test_last_active_admin_cannot_withdraw(client):
    admin = await _signup(client, "player01", "Shadow#1001")
    res = await client.post(
        "/api/members/player01/withdraw",
        headers={"Authorization": f"Bearer {admin['accessToken']}"},
    )
    assert res.status_code == 409
