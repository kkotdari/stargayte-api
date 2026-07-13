"""도전장("너 나와!") 게시판 스모크 테스트."""


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


async def _approve(client, admin_token: str, member_id: str) -> None:
    res = await client.patch(
        f"/api/members/{member_id}/status",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "active"},
    )
    assert res.status_code == 200, res.text


async def test_create_single_target_is_1v1_and_pending(client):
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    headers = {"Authorization": f"Bearer {a['accessToken']}"}

    res = await client.post(
        "/api/challenges", headers=headers,
        json={"targetMemberIds": ["bob"], "message": "한판 하실래요"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["matchType"] == "0101"
    assert body["status"] == "pending"
    assert body["message"] == "한판 하실래요"
    assert [t["memberId"] for t in body["targets"]] == ["bob"]
    assert body["targets"][0]["response"] == "pending"


async def test_multi_target_is_team_type_and_requires_all_accepts(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    c = await _signup(client, "carol", "Carol#1003")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    headers_c = {"Authorization": f"Bearer {c['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")
    await _approve(client, a["accessToken"], "carol")

    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob", "carol"]},
    )
    assert res.status_code == 200, res.text
    challenge_id = res.json()["id"]
    assert res.json()["matchType"] == "0102"

    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "pending"  # carol이 아직 응답 안 함

    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_c,
        json={"response": "accepted"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "confirmed"


async def test_any_rejection_marks_challenge_rejected(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    c = await _signup(client, "carol", "Carol#1003")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    headers_c = {"Authorization": f"Bearer {c['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")
    await _approve(client, a["accessToken"], "carol")

    res = await client.post(
        "/api/challenges", headers=headers_a, json={"targetMemberIds": ["bob", "carol"]},
    )
    challenge_id = res.json()["id"]

    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "rejected"},
    )
    assert res.json()["status"] == "rejected"

    # carol이 나중에 승락해도 이미 거부된 초대장은 그대로 거부다.
    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_c,
        json={"response": "accepted"},
    )
    assert res.json()["status"] == "rejected"


async def test_cannot_respond_twice(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a, json={"targetMemberIds": ["bob"]},
    )
    challenge_id = res.json()["id"]

    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted"},
    )
    assert res.status_code == 200, res.text

    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "rejected"},
    )
    assert res.status_code == 400, res.text


async def test_non_target_cannot_respond(client):
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    c = await _signup(client, "carol", "Carol#1003")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_c = {"Authorization": f"Bearer {c['accessToken']}"}
    await _approve(client, a["accessToken"], "carol")

    res = await client.post(
        "/api/challenges", headers=headers_a, json={"targetMemberIds": ["bob"]},
    )
    challenge_id = res.json()["id"]

    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_c,
        json={"response": "accepted"},
    )
    assert res.status_code == 403, res.text


async def test_cannot_target_self(client):
    a = await _signup(client, "alice", "Alice#1001")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}

    res = await client.post(
        "/api/challenges", headers=headers_a, json={"targetMemberIds": ["alice"]},
    )
    assert res.status_code == 400, res.text


async def test_pending_for_me_returns_once_then_marks_notified(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    await client.post("/api/challenges", headers=headers_a, json={"targetMemberIds": ["bob"]})

    res = await client.get("/api/challenges/pending-for-me", headers=headers_b)
    assert res.status_code == 200, res.text
    assert len(res.json()["items"]) == 1

    # 이미 알림을 봤으니 다시 조회하면 비어 있어야 한다(목록 자체에서는 계속 보이지만,
    # 팝업 대상에서는 한 번만 잡힌다).
    res = await client.get("/api/challenges/pending-for-me", headers=headers_b)
    assert res.json()["items"] == []

    res = await client.get("/api/challenges", headers=headers_b)
    assert len(res.json()["items"]) == 1


async def test_attach_result_requires_confirmed_status(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post("/api/challenges", headers=headers_a, json={"targetMemberIds": ["bob"]})
    challenge_id = res.json()["id"]

    match_res = await client.post(
        "/api/matches", headers=headers_a,
        json={
            "date": "2026-07-09",
            "team1": [{"memberId": "alice", "race": "테란"}],
            "team2": [{"memberId": "bob", "race": "저그"}],
            "status": "completed",
            "result": "team1",
        },
    )
    assert match_res.status_code == 200, match_res.text
    match_id = match_res.json()["id"]

    # 아직 아무도 승락하지 않아 pending 상태 — 결과 연결이 막혀야 한다.
    res = await client.post(
        f"/api/challenges/{challenge_id}/attach-result", headers=headers_a,
        json={"matchId": match_id},
    )
    assert res.status_code == 400, res.text

    await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted"},
    )

    res = await client.post(
        f"/api/challenges/{challenge_id}/attach-result", headers=headers_a,
        json={"matchId": match_id},
    )
    assert res.status_code == 200, res.text
    assert res.json()["resultMatchId"] == match_id


async def test_attach_result_allows_non_participant(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    c = await _signup(client, "carol", "Carol#1003")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    headers_c = {"Authorization": f"Bearer {c['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")
    await _approve(client, a["accessToken"], "carol")

    res = await client.post("/api/challenges", headers=headers_a, json={"targetMemberIds": ["bob"]})
    challenge_id = res.json()["id"]
    await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b, json={"response": "accepted"},
    )

    match_res = await client.post(
        "/api/matches", headers=headers_c,
        json={
            "date": "2026-07-09",
            "team1": [{"memberId": "alice", "race": "테란"}],
            "team2": [{"memberId": "bob", "race": "저그"}],
            "status": "completed",
            "result": "team1",
        },
    )
    assert match_res.status_code == 200, match_res.text
    match_id = match_res.json()["id"]

    # 참가자(alice/bob)가 아닌 carol도 결과를 연결할 수 있어야 한다 — 리플레이 등록/
    # 게임아이디 매핑을 참가자 전용에서 아무나 도울 수 있도록 권한을 확장했다.
    res = await client.post(
        f"/api/challenges/{challenge_id}/attach-result", headers=headers_c,
        json={"matchId": match_id},
    )
    assert res.status_code == 200, res.text
    assert res.json()["resultMatchId"] == match_id


async def test_creator_can_cancel_pending_challenge(client):
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}

    res = await client.post(
        "/api/challenges", headers=headers_a, json={"targetMemberIds": ["bob"]},
    )
    challenge_id = res.json()["id"]

    res = await client.post(f"/api/challenges/{challenge_id}/cancel", headers=headers_a)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "canceled"


async def test_non_creator_cannot_cancel(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a, json={"targetMemberIds": ["bob"]},
    )
    challenge_id = res.json()["id"]

    res = await client.post(f"/api/challenges/{challenge_id}/cancel", headers=headers_b)
    assert res.status_code == 403, res.text


async def test_cannot_cancel_after_confirmed(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a, json={"targetMemberIds": ["bob"]},
    )
    challenge_id = res.json()["id"]
    await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted"},
    )

    res = await client.post(f"/api/challenges/{challenge_id}/cancel", headers=headers_a)
    assert res.status_code == 400, res.text


async def test_reject_reason_visible_only_to_creator(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    c = await _signup(client, "carol", "Carol#1003")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    headers_c = {"Authorization": f"Bearer {c['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")
    await _approve(client, a["accessToken"], "carol")

    res = await client.post(
        "/api/challenges", headers=headers_a, json={"targetMemberIds": ["bob"]},
    )
    challenge_id = res.json()["id"]

    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "rejected", "reason": "그날은 바빠요"},
    )
    assert res.status_code == 200, res.text
    # respond() 자신의 응답은 viewer=bob(요청자가 아님)으로 직렬화되므로 여기서는
    # 굳이 확인하지 않는다 — 가림 규칙은 아래처럼 "다른 사람이 목록을 조회할 때" 확인한다.

    res = await client.get("/api/challenges", headers=headers_a)
    body = next(c for c in res.json()["items"] if c["id"] == challenge_id)
    assert body["targets"][0]["rejectReason"] == "그날은 바빠요"

    res = await client.get("/api/challenges", headers=headers_c)
    body = next(c for c in res.json()["items"] if c["id"] == challenge_id)
    assert body["targets"][0]["rejectReason"] is None


async def test_reapply_resets_target_responses_and_can_edit_time(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledAt": "2026-08-01T10:00:00Z"},
    )
    challenge_id = res.json()["id"]
    await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "rejected", "reason": "그날은 바빠요"},
    )

    # 확정되지 않은 한 재신청 전에도 취소는 여전히 가능해야 하므로 별도로 막지 않는다 —
    # 여기서는 곧장 재신청부터 확인한다.
    res = await client.post(
        f"/api/challenges/{challenge_id}/reapply", headers=headers_a,
        json={"scheduledAt": "2026-08-05T12:00:00Z", "message": "이번엔 어때요"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "pending"
    assert body["scheduledAt"].startswith("2026-08-05")
    assert body["message"] == "이번엔 어때요"
    assert body["targets"][0]["response"] == "pending"
    assert body["targets"][0]["rejectReason"] is None

    # 다시 승락하면 정상적으로 확정된다.
    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "confirmed"


async def test_reapply_without_edits_keeps_existing_time_and_message(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledAt": "2026-08-01T10:00:00Z", "message": "원래 메모"},
    )
    challenge_id = res.json()["id"]
    await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b, json={"response": "rejected"},
    )

    res = await client.post(f"/api/challenges/{challenge_id}/reapply", headers=headers_a, json={})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["scheduledAt"].startswith("2026-08-01")
    assert body["message"] == "원래 메모"


async def test_only_creator_can_reapply_and_only_when_rejected(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a, json={"targetMemberIds": ["bob"]},
    )
    challenge_id = res.json()["id"]

    # 아직 pending(거절되지 않음) — 재신청 불가.
    res = await client.post(f"/api/challenges/{challenge_id}/reapply", headers=headers_a, json={})
    assert res.status_code == 400, res.text

    await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b, json={"response": "rejected"},
    )

    # 요청자가 아니면 거절된 뒤라도 재신청할 수 없다.
    res = await client.post(f"/api/challenges/{challenge_id}/reapply", headers=headers_b, json={})
    assert res.status_code == 403, res.text


async def test_own_team_members_are_included_and_marks_team_type(client):
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "dave", "Dave#1004")
    await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    await _approve(client, a["accessToken"], "dave")
    await _approve(client, a["accessToken"], "bob")

    # 상대는 1명뿐이어도 내 팀에 1명 더 있으면(2v1) 1:1이 아니라 팀전이다.
    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "ownTeamMemberIds": ["dave"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["matchType"] == "0102"
    assert [m["memberId"] for m in body["ownMembers"]] == ["dave"]


async def test_cannot_include_self_in_own_team(client):
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}

    # 본인은 이미 자동 포함이니 명시적으로 넣는 건 서비스 레벨에서 막힌다(400).
    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "ownTeamMemberIds": ["alice"]},
    )
    assert res.status_code == 400, res.text


async def test_cannot_put_same_member_on_both_teams(client):
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}

    # 상대 팀/내 팀 중복은 스키마 레벨(model_validator)에서 막혀 422로 응답한다.
    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "ownTeamMemberIds": ["bob"]},
    )
    assert res.status_code == 422, res.text
