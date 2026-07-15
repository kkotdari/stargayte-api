"""도전장("너 나와!") 게시판 스모크 테스트."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.domain.challenges.models import Challenge


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
        json={"targetMemberIds": ["bob", "carol"], "scheduledAt": "2026-08-01T10:00:00Z"},
    )
    assert res.status_code == 200, res.text
    challenge_id = res.json()["id"]
    assert res.json()["matchType"] == "0102"

    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "pending"  # carol이 아직 응답 안 함

    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_c,
        json={"response": "accepted", "reason": "OK!"},
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
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob", "carol"], "scheduledAt": "2026-08-01T10:00:00Z"},
    )
    challenge_id = res.json()["id"]

    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "rejected", "reason": "다음에 해요"},
    )
    assert res.json()["status"] == "rejected"

    # carol이 나중에 승락해도 이미 거부된 초대장은 그대로 거부다.
    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_c,
        json={"response": "accepted", "reason": "OK!"},
    )
    assert res.json()["status"] == "rejected"


async def test_cannot_respond_twice(client):
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

    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!"},
    )
    assert res.status_code == 200, res.text

    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "rejected", "reason": "다음에 해요"},
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
        json={"response": "accepted", "reason": "OK!"},
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
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledAt": "2026-08-01T10:00:00Z"},
    )
    challenge_id = res.json()["id"]
    await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!"},
    )

    res = await client.post(f"/api/challenges/{challenge_id}/cancel", headers=headers_a)
    assert res.status_code == 400, res.text


async def test_reject_reason_is_visible_to_anyone(client):
    """응답 한마디(수락/거절)는 전체 공개다 — 요청자든 제3자든 똑같이 볼 수 있다
    (요청: "요청자 계정이 아니더라도 다 보여야해 전체 공개")."""
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
    assert res.json()["targets"][0]["responseMessage"] == "그날은 바빠요"

    res = await client.get("/api/challenges", headers=headers_a)
    body = next(c for c in res.json()["items"] if c["id"] == challenge_id)
    assert body["targets"][0]["responseMessage"] == "그날은 바빠요"

    # 요청자도, 지목된 당사자도 아닌 제3자(carol)에게도 똑같이 보인다.
    res = await client.get("/api/challenges", headers=headers_c)
    body = next(c for c in res.json()["items"] if c["id"] == challenge_id)
    assert body["targets"][0]["responseMessage"] == "그날은 바빠요"


async def test_reapply_creates_new_challenge_linked_to_original(client):
    """요청: "재신청하면 원래건은 종료되고 새로운 도전 행이 만들어져 새 아이디로...
    refer라던지 그런 느낌의 컬럼을 만들어서 어디서 이어졌는지 저장해둬" — 원래 행은
    안 건드리고(거절 상태 그대로) 새 id의 도전장이 생기며, reappliedFromId가 원래
    id를 가리키고 history에 원래 기록이 담긴다."""
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledAt": "2026-08-01T10:00:00Z"},
    )
    original_id = res.json()["id"]
    await client.post(
        f"/api/challenges/{original_id}/respond", headers=headers_b,
        json={"response": "rejected", "reason": "그날은 바빠요"},
    )

    res = await client.post(
        f"/api/challenges/{original_id}/reapply", headers=headers_a,
        json={"scheduledAt": "2026-08-05T12:00:00Z", "message": "이번엔 어때요"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    new_id = body["id"]
    assert new_id != original_id
    assert body["reappliedFromId"] == original_id
    assert body["status"] == "pending"
    assert body["scheduledAt"].startswith("2026-08-05")
    assert body["message"] == "이번엔 어때요"
    assert body["targets"][0]["response"] == "pending"
    assert body["targets"][0]["responseMessage"] is None
    assert len(body["history"]) == 1
    assert body["history"][0]["id"] == original_id
    assert body["history"][0]["status"] == "rejected"
    assert body["history"][0]["targets"][0]["responseMessage"] == "그날은 바빠요"

    # 원래 도전장은 그대로 거절 상태로 남아있다(더 이상 응답할 수 없다 — 새 행이
    # 그 자리를 대신한다).
    res = await client.post(
        f"/api/challenges/{original_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!"},
    )
    assert res.status_code == 400, res.text

    # 새 도전장에 승락하면 정상적으로 확정된다.
    res = await client.post(
        f"/api/challenges/{new_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "confirmed"

    # 목록에는 새 도전장만 보이고 원래 도전장은 안 보인다(요청: "최신 1건만 목록에
    # 나오고, 카드 안에서 좌우로 슬라이드해 이전 기록을 본다").
    res = await client.get("/api/challenges", headers=headers_a)
    ids = [c["id"] for c in res.json()["items"]]
    assert new_id in ids
    assert original_id not in ids


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
        f"/api/challenges/{challenge_id}/respond", headers=headers_b, json={"response": "rejected", "reason": "다음에 해요"},
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
        f"/api/challenges/{challenge_id}/respond", headers=headers_b, json={"response": "rejected", "reason": "다음에 해요"},
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


async def test_accepting_unscheduled_challenge_requires_time(client):
    """요청자가 "시간 지정"을 끄고 보낸(scheduledAt 없음) 도전장은 "상대가 정해도
    된다"는 뜻이다 — 그 시간을 아무도 안 채우면 영원히 미정인 채 승락 상태에 박제되는
    문제가 있었다(요청: "도전자/상대 모두 시간을 지정하지 않았는데 수락이 된 경우가
    있네 이러면 안되는데"). 수락하는 시점에 상대가 시간을 정하지 않으면 거부돼야 한다."""
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a, json={"targetMemberIds": ["bob"]},
    )
    challenge_id = res.json()["id"]
    assert res.json()["scheduledAt"] is None

    # 시간 없이 그냥 승락하려 하면 막혀야 한다.
    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!"},
    )
    assert res.status_code == 400, res.text

    # 시간을 같이 보내면 그 시간으로 정해지며 정상 승락된다.
    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!", "scheduledAt": "2026-08-01T10:00:00Z"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "confirmed"
    assert body["scheduledAt"].startswith("2026-08-01")


async def test_accepting_scheduled_challenge_ignores_target_supplied_time(client):
    """요청자가 이미 시간을 정해 보낸 도전장은, 응답하는 쪽이 다른 시간을 같이 보내도
    무시되고 원래 시간 그대로 유지돼야 한다(응답하는 쪽이 요청자의 시간을 바꿀 수는
    없다)."""
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

    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!", "scheduledAt": "2026-12-25T10:00:00Z"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["scheduledAt"].startswith("2026-08-01")


async def test_canceled_challenge_is_hidden_from_list(client):
    """취소된 도전장은 디비엔 남지만(상태값만 취소) 목록엔 안 보인다(요청: "도전장
    취소시 삭제(디비에 있지만 상태값을 삭제로). 화면에 미노출")."""
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}

    res = await client.post(
        "/api/challenges", headers=headers_a, json={"targetMemberIds": ["bob"]},
    )
    challenge_id = res.json()["id"]
    await client.post(f"/api/challenges/{challenge_id}/cancel", headers=headers_a)

    res = await client.get("/api/challenges", headers=headers_a)
    ids = [c["id"] for c in res.json()["items"]]
    assert challenge_id not in ids


async def test_reapply_blocked_before_expiry_but_allowed_after(client, db_session):
    """응답 없이 기한(1일)이 지나면(요청: "기한내 미응답시 재신청 가능") pending 상태
    그대로도 재신청할 수 있다 — 기한이 안 지났으면 거절도 안 됐고 만료도 아니라 여전히 막힌다."""
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}

    res = await client.post(
        "/api/challenges", headers=headers_a, json={"targetMemberIds": ["bob"]},
    )
    challenge_id = res.json()["id"]

    res = await client.post(f"/api/challenges/{challenge_id}/reapply", headers=headers_a, json={})
    assert res.status_code == 400, res.text

    # created_at을 기한(1일) 하고도 1시간 전으로 되돌려 "기한 내 무응답"을 재현한다.
    await db_session.execute(
        update(Challenge).where(Challenge.id == challenge_id).values(
            created_at=datetime.now(UTC) - timedelta(days=1, hours=1)
        )
    )
    await db_session.commit()

    res = await client.post(f"/api/challenges/{challenge_id}/reapply", headers=headers_a, json={})
    assert res.status_code == 200, res.text
    assert res.json()["reappliedFromId"] == challenge_id
    assert res.json()["chainKind"] == "reapply"


async def test_reapply_blocked_when_already_chained(client):
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
        json={"response": "rejected", "reason": "다음에 해요"},
    )
    await client.post(f"/api/challenges/{challenge_id}/reapply", headers=headers_a, json={})

    # 이미 한 번 재신청해서 이어졌으니, 원래 건으로 또 재신청할 수는 없다.
    res = await client.post(f"/api/challenges/{challenge_id}/reapply", headers=headers_a, json={})
    assert res.status_code == 400, res.text


async def test_only_first_target_responder_can_leave_a_message(client):
    """한마디는 최초응답자만 가능(요청) — 팀전에서 지목된 두 번째 사람이 응답하며
    남긴 한마디는 저장되지 않는다."""
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
        json={"targetMemberIds": ["bob", "carol"], "scheduledAt": "2026-08-01T10:00:00Z"},
    )
    challenge_id = res.json()["id"]

    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "제가 먼저 응답!"},
    )
    bob_target = next(t for t in res.json()["targets"] if t["memberId"] == "bob")
    assert bob_target["responseMessage"] == "제가 먼저 응답!"

    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_c,
        json={"response": "accepted", "reason": "저도 할래요"},
    )
    assert res.status_code == 200, res.text
    carol_target = next(t for t in res.json()["targets"] if t["memberId"] == "carol")
    assert carol_target["responseMessage"] is None


async def test_enter_result_blocked_before_confirmed_or_before_schedule_passes(client):
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

    # 아직 pending — 결과 입력 불가.
    res = await client.post(
        f"/api/challenges/{challenge_id}/result", headers=headers_a, json={"winnerSide": "creator"},
    )
    assert res.status_code == 400, res.text

    await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!"},
    )

    # 확정은 됐지만 예정 일시(2026-08-01)가 아직 안 지났다.
    res = await client.post(
        f"/api/challenges/{challenge_id}/result", headers=headers_a, json={"winnerSide": "creator"},
    )
    assert res.status_code == 400, res.text


async def test_enter_result_first_submission_wins_and_locks(client):
    """참가자 누구든 먼저 입력하는 쪽이 그대로 인정되고, 이미 입력된 뒤엔 다시 입력할
    수 없다(요청: "먼저 입력하는 쪽 인정")."""
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
        json={"targetMemberIds": ["bob"], "scheduledAt": "2020-01-01T10:00:00Z"},
    )
    challenge_id = res.json()["id"]
    await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!"},
    )

    # 참가자가 아닌 사람은 결과를 입력할 수 없다.
    res = await client.post(
        f"/api/challenges/{challenge_id}/result", headers=headers_c, json={"winnerSide": "creator"},
    )
    assert res.status_code == 403, res.text

    res = await client.post(
        f"/api/challenges/{challenge_id}/result", headers=headers_b, json={"winnerSide": "target"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["resultWinnerSide"] == "target"

    # 이미 결과가 입력됐으니 다시(다른 값으로도) 입력할 수 없다.
    res = await client.post(
        f"/api/challenges/{challenge_id}/result", headers=headers_a, json={"winnerSide": "creator"},
    )
    assert res.status_code == 400, res.text


async def test_revenge_challenge_only_by_losing_side_and_links_chain(client):
    """완료된 대결에서 패배한 쪽만 설욕전을 신청할 수 있고, 신청하면 패배한 쪽이 새
    도전장의 요청자가 되며 원래 대결과 체인으로 이어진다(요청: "완료시 패배한 쪽에서
    설욕전 신청 가능... 너나와 체인으로 연결")."""
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledAt": "2020-01-01T10:00:00Z"},
    )
    original_id = res.json()["id"]
    await client.post(
        f"/api/challenges/{original_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!"},
    )
    # alice(creator)가 이겼다 — bob(target)이 패배한 쪽.
    await client.post(
        f"/api/challenges/{original_id}/result", headers=headers_a, json={"winnerSide": "creator"},
    )

    # 이긴 쪽(alice)은 설욕전을 신청할 수 없다.
    res = await client.post(
        f"/api/challenges/{original_id}/revenge", headers=headers_a, json={},
    )
    assert res.status_code == 403, res.text

    # 패배한 쪽(bob)은 신청할 수 있고, bob이 새 도전장의 요청자가 된다.
    res = await client.post(
        f"/api/challenges/{original_id}/revenge", headers=headers_b,
        json={"scheduledAt": "2026-09-01T10:00:00Z", "message": "이번엔 진짜 설욕한다"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["reappliedFromId"] == original_id
    assert body["chainKind"] == "revenge"
    assert body["createdBy"]["id"] == "bob"
    assert [t["memberId"] for t in body["targets"]] == ["alice"]

    # 원래 대결은 목록에서 더 안 보인다(체인은 최신건 기준으로만 노출).
    res = await client.get("/api/challenges", headers=headers_a)
    ids = [c["id"] for c in res.json()["items"]]
    assert original_id not in ids
    assert body["id"] in ids


async def test_postpone_confirmed_challenge_resets_result_and_allows_either_side(client):
    """수락된 대결은 도전자/상대 누구든 연기할 수 있고, 예정 일시가 지난 뒤에도
    가능하다(요청). 잘못 입력됐을 수 있는 기존 결과는 새 일정으로 초기화된다."""
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledAt": "2020-01-01T10:00:00Z"},
    )
    challenge_id = res.json()["id"]

    # 아직 확정 전엔 연기할 수 없다.
    res = await client.post(
        f"/api/challenges/{challenge_id}/postpone", headers=headers_a,
        json={"scheduledAt": "2026-09-01T10:00:00Z"},
    )
    assert res.status_code == 400, res.text

    await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!"},
    )
    await client.post(
        f"/api/challenges/{challenge_id}/result", headers=headers_a, json={"winnerSide": "creator"},
    )

    # 상대(bob)가 연기해도 되고(도전자만 되는 게 아니다), 기존 결과는 초기화된다.
    res = await client.post(
        f"/api/challenges/{challenge_id}/postpone", headers=headers_b,
        json={"scheduledAt": "2026-09-01T10:00:00Z"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["scheduledAt"].startswith("2026-09-01")
    assert body["resultWinnerSide"] is None
    assert body["status"] == "confirmed"


async def test_result_pending_for_me_returns_once_then_marks_result_notified(client):
    """"결과 입력" 팝업 큐 — 예정 일시가 지난 미입력 확정 대결이 참가자별로 한 번만
    내려오고(내려주는 즉시 result_notified), 결과가 입력된 대결은 애초에 안 잡힌다
    (요청: "결과 입력 팝업 확인 여부는 디비에 관리")."""
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledAt": "2020-01-01T10:00:00Z"},
    )
    challenge_id = res.json()["id"]

    # 아직 확정 전(응답 대기) — 팝업 대상이 아니다.
    res = await client.get("/api/challenges/result-pending-for-me", headers=headers_a)
    assert res.status_code == 200, res.text
    assert res.json()["items"] == []

    await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!"},
    )

    # 확정 + 예정 일시(2020년) 경과 + 결과 미입력 — 양쪽 참가자 모두에게 각각 한 번씩 잡힌다.
    res = await client.get("/api/challenges/result-pending-for-me", headers=headers_a)
    assert [c["id"] for c in res.json()["items"]] == [challenge_id]
    res = await client.get("/api/challenges/result-pending-for-me", headers=headers_b)
    assert [c["id"] for c in res.json()["items"]] == [challenge_id]

    # 이미 봤으니 다시 조회하면 비어 있다 — 프론트를 갈아엎어도(기기/브라우저가 바뀌어도)
    # 서버가 기억하므로 재등장하지 않는다.
    res = await client.get("/api/challenges/result-pending-for-me", headers=headers_a)
    assert res.json()["items"] == []


async def test_result_pending_for_me_skips_future_schedule_and_entered_result(client):
    """예정 일시가 아직 안 지난 확정 대결은 팝업 대상이 아니고(표시도 하지 않아 나중에
    일시가 지나면 그때 잡힌다), 결과가 이미 입력된 대결도 잡히지 않는다."""
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    # 미래 일시로 확정된 대결 — 아직 팝업 대상이 아니다.
    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledAt": "2099-01-01T10:00:00Z"},
    )
    future_id = res.json()["id"]
    await client.post(
        f"/api/challenges/{future_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!"},
    )
    res = await client.get("/api/challenges/result-pending-for-me", headers=headers_a)
    assert res.json()["items"] == []

    # 지난 일시 + 결과까지 이미 입력된 대결 — 역시 팝업 대상이 아니다.
    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledAt": "2020-01-01T10:00:00Z"},
    )
    done_id = res.json()["id"]
    await client.post(
        f"/api/challenges/{done_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!"},
    )
    await client.post(
        f"/api/challenges/{done_id}/result", headers=headers_a, json={"winnerSide": "creator"},
    )
    res = await client.get("/api/challenges/result-pending-for-me", headers=headers_b)
    assert res.json()["items"] == []


async def test_pending_for_me_excludes_canceled_challenge(client):
    """상대가 초대 팝업을 보기 전에 요청자가 취소하면, 그 초대는 팝업 큐에 아예 안
    잡힌다 — 수락을 눌러도 400만 나는 죽은 초대가 한 번 뜨던 문제의 회귀 테스트."""
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post("/api/challenges", headers=headers_a, json={"targetMemberIds": ["bob"]})
    challenge_id = res.json()["id"]
    res = await client.post(f"/api/challenges/{challenge_id}/cancel", headers=headers_a)
    assert res.status_code == 200, res.text

    res = await client.get("/api/challenges/pending-for-me", headers=headers_b)
    assert res.status_code == 200, res.text
    assert res.json()["items"] == []
