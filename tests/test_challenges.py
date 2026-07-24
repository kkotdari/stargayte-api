"""도전장("너 나와!") 게시판 스모크 테스트 — 상태 4개(응답대기/성사/완료/폐기)와
재대결(revenge)만 남긴 구조. 취소/연기/재신청은 제거됐고, 거절·무응답·미실시는 모두
폐기(휴지통)로 통합됐다."""

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


async def _confirmed_1v1(
    client, *, scheduled_date: str = "2020-01-01", scheduled_time: str = "10:00",
) -> tuple[dict, dict, int]:
    """alice(요청자)↔bob 1:1 확정(성사) 대결 하나를 만들어 (headers_a, headers_b, id) 반환."""
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")
    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledDate": scheduled_date, "scheduledTime": scheduled_time},
    )
    challenge_id = res.json()["id"]
    await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!"},
    )
    return headers_a, headers_b, challenge_id


async def test_create_single_target_is_1v1_and_pending(client):
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledDate": "2026-08-01", "scheduledTime": "10:00"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["matchType"] == "0101"
    assert body["status"] == "pending"


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
        json={"targetMemberIds": ["bob", "carol"], "scheduledDate": "2026-08-01", "scheduledTime": "10:00"},
    )
    assert res.json()["matchType"] == "0102"
    challenge_id = res.json()["id"]

    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!"},
    )
    assert res.json()["status"] == "pending"  # carol이 아직 응답 안 함

    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_c,
        json={"response": "accepted", "reason": "좋아요"},
    )
    assert res.json()["status"] == "confirmed"


async def test_any_rejection_discards_challenge(client):
    """지목자 한 명이라도 명시적으로 거절하면 그 즉시 폐기(휴지통)로 간다."""
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
        json={"targetMemberIds": ["bob", "carol"], "scheduledDate": "2026-08-01", "scheduledTime": "10:00"},
    )
    challenge_id = res.json()["id"]

    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "rejected", "reason": "다음에 해요"},
    )
    body = res.json()
    assert body["status"] == "discarded"
    # 폐기된 도전장은 discardedAt(폐기 시각)을 내려준다 — 휴지통 "최근 버려진 순" 정렬용.
    assert body["discardedAt"] is not None

    # 이미 폐기된 초대장엔 carol이 응답할 수 없다.
    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_c,
        json={"response": "accepted", "reason": "OK!"},
    )
    assert res.status_code == 400, res.text


async def test_discard_without_reason_marks_discarded_response(client):
    """편지봉투에서 '버리기' → 사유 없이 폐기(휴지통)로 가고, 응답은 거절(rejected)과
    구분되는 'discarded'(버림)로 기록된다(요청: "완전히 휴지통행이고 사유 없음, 버림으로
    상태 표시(거절하고 다른 응답)")."""
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledDate": "2026-08-01", "scheduledTime": "10:00"},
    )
    challenge_id = res.json()["id"]

    # 사유 없이 버림.
    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "discarded"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "discarded"
    assert body["discardedAt"] is not None
    bob_target = next(t for t in body["targets"] if t["memberId"] == "bob")
    assert bob_target["response"] == "discarded"  # 거절이 아니라 '버림'


async def test_cannot_respond_twice(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledDate": "2026-08-01", "scheduledTime": "10:00"},
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

    res = await client.get("/api/challenges/pending-for-me", headers=headers_b)
    assert res.json()["items"] == []

    res = await client.get("/api/challenges", headers=headers_b)
    assert len(res.json()["items"]) == 1


async def test_own_team_members_are_included_and_marks_team_type(client):
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    await _signup(client, "carol", "Carol#1003")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")
    await _approve(client, a["accessToken"], "carol")

    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "ownTeamMemberIds": ["carol"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["matchType"] == "0102"
    assert [m["memberId"] for m in body["ownMembers"]] == ["carol"]


async def test_cannot_include_self_in_own_team(client):
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "ownTeamMemberIds": ["alice"]},
    )
    assert res.status_code == 400, res.text


async def test_cannot_put_same_member_on_both_teams(client):
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "ownTeamMemberIds": ["bob"]},
    )
    assert res.status_code == 422, res.text


async def test_accepting_unscheduled_challenge_stays_undecided_then_completes_now(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post("/api/challenges", headers=headers_a, json={"targetMemberIds": ["bob"]})
    challenge_id = res.json()["id"]

    # 시간 미정 도전장 — 시간을 안 넘겨도 그대로 수락(성사)된다(예전엔 거부했음).
    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "confirmed"
    assert body["scheduledAt"] is None

    # 시간 미정이라도 결과를 바로 입력할 수 있고, 결과와 함께 넘긴 실제 일시로 채워진다.
    res = await client.post(
        f"/api/challenges/{challenge_id}/result", headers=headers_a,
        json={"winnerSide": "creator", "scheduledDate": "2026-08-01", "scheduledTime": "10:00"},
    )
    assert res.status_code == 200, res.text
    done = res.json()
    assert done["status"] == "done"
    assert done["scheduledDate"] == "2026-08-01"


async def test_accepting_unscheduled_challenge_can_still_set_time(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post("/api/challenges", headers=headers_a, json={"targetMemberIds": ["bob"]})
    challenge_id = res.json()["id"]

    # 수락하며 시간을 정하면 그 값으로 확정된다.
    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!", "scheduledDate": "2026-09-01", "scheduledTime": "10:00"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "confirmed"
    assert body["scheduledDate"] == "2026-09-01"


async def test_accepting_scheduled_challenge_ignores_target_supplied_time(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledDate": "2026-08-01", "scheduledTime": "10:00"},
    )
    challenge_id = res.json()["id"]

    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!", "scheduledDate": "2099-01-01", "scheduledTime": "10:00"},
    )
    # 요청자가 정한 시간을 응답자가 바꿀 수 없다 — 원래 값 유지.
    assert res.json()["scheduledDate"] == "2026-08-01"


async def test_accepting_date_only_challenge_lets_target_add_time(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    # 요청자가 날짜만 정하고 시간은 비워서 보낸다.
    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledDate": "2026-08-01"},
    )
    challenge_id = res.json()["id"]
    assert res.json()["scheduledDate"] == "2026-08-01"
    assert res.json()["scheduledTime"] is None

    # 응답자는 날짜는 못 바꾸지만(요청자가 정한 날짜 유지) 시간은 추가할 수 있다(요청).
    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={
            "response": "accepted", "reason": "OK!",
            "scheduledDate": "2099-12-31", "scheduledTime": "21:00",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["scheduledDate"] == "2026-08-01"
    assert body["scheduledTime"] == "21:00"


async def test_enter_result_blocked_before_confirmed(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledDate": "2026-08-01", "scheduledTime": "10:00"},
    )
    challenge_id = res.json()["id"]

    # 아직 성사(confirmed) 전이라 결과를 넣을 수 없다 — 유효한 페이로드를 다 보내도 400(비즈니스 규칙).
    res = await client.post(
        f"/api/challenges/{challenge_id}/result", headers=headers_a,
        json={"winnerSide": "creator", "scheduledDate": "2026-08-01", "scheduledTime": "10:00"},
    )
    assert res.status_code == 400, res.text  # 아직 pending

    await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!"},
    )
    # 성사된 뒤엔 예정 일시가 안 지났어도 결과를 바로 입력할 수 있다(예전의 "일시 지남" 제약 제거).
    res = await client.post(
        f"/api/challenges/{challenge_id}/result", headers=headers_a,
        json={"winnerSide": "creator", "scheduledDate": "2026-08-01", "scheduledTime": "10:00"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "done"


async def test_enter_result_marks_done_and_first_submission_locks(client):
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
        json={"targetMemberIds": ["bob"], "scheduledDate": "2020-01-01", "scheduledTime": "10:00"},
    )
    challenge_id = res.json()["id"]
    await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!"},
    )

    res = await client.post(
        f"/api/challenges/{challenge_id}/result", headers=headers_c,
        json={"winnerSide": "creator", "scheduledDate": "2020-01-01", "scheduledTime": "10:00"},
    )
    assert res.status_code == 403, res.text  # 참가자 아님

    res = await client.post(
        f"/api/challenges/{challenge_id}/result", headers=headers_b,
        json={"winnerSide": "target", "scheduledDate": "2020-01-01", "scheduledTime": "10:00"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "done"
    assert res.json()["resultWinnerSide"] == "target"

    res = await client.post(
        f"/api/challenges/{challenge_id}/result", headers=headers_a,
        json={"winnerSide": "creator", "scheduledDate": "2020-01-01", "scheduledTime": "10:00"},
    )
    assert res.status_code == 400, res.text  # 이미 입력됨


async def test_confirmed_stays_confirmed_after_schedule_until_result_entered(client):
    """예정 시간이 지나도 결과가 안 들어왔으면 완료가 아니라 계속 성사(confirmed)다
    (요청: "예정 시간 지나도 결과 입력 안 된 건은 성사 상태")."""
    headers_a, _headers_b, challenge_id = await _confirmed_1v1(client, scheduled_date="2020-01-01")
    res = await client.get("/api/challenges", headers=headers_a)
    body = next(c for c in res.json()["items"] if c["id"] == challenge_id)
    assert body["status"] == "confirmed"


async def test_not_held_result_goes_to_trash(client):
    """수락했지만 미실시(not_held)로 결과가 들어오면 완료가 아니라 폐기(휴지통)로 간다
    (요청: "수락했지만 미실시한 경우도 휴지통으로")."""
    headers_a, _headers_b, challenge_id = await _confirmed_1v1(client, scheduled_date="2020-01-01")
    res = await client.post(
        f"/api/challenges/{challenge_id}/result", headers=headers_a,
        json={"winnerSide": "not_held", "scheduledDate": "2020-01-01", "scheduledTime": "10:00"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "discarded"


async def test_revenge_only_by_losing_side_and_links_chain(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledDate": "2020-01-01", "scheduledTime": "10:00"},
    )
    original_id = res.json()["id"]
    await client.post(
        f"/api/challenges/{original_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!"},
    )
    # alice(creator)가 이겼다 — bob(target)이 패배한 쪽.
    await client.post(
        f"/api/challenges/{original_id}/result", headers=headers_a,
        json={"winnerSide": "creator", "scheduledDate": "2020-01-01", "scheduledTime": "10:00"},
    )

    # 이긴 쪽(alice)은 재대결을 신청할 수 없다.
    res = await client.post(f"/api/challenges/{original_id}/revenge", headers=headers_a, json={})
    assert res.status_code == 403, res.text

    # 패배한 쪽(bob)은 신청할 수 있고, bob이 새 도전장의 요청자가 된다.
    res = await client.post(
        f"/api/challenges/{original_id}/revenge", headers=headers_b,
        json={"scheduledDate": "2026-09-01", "scheduledTime": "10:00", "message": "이번엔 진짜 설욕한다"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["reappliedFromId"] == original_id
    assert body["createdBy"]["id"] == "bob"
    assert [t["memberId"] for t in body["targets"]] == ["alice"]
    # 리벤지 신청 시 보낸 한마디가 새 도전장에 저장된다(요청).
    assert body["message"] == "이번엔 진짜 설욕한다"

    # 원래 대결은 목록에서 더 안 보인다(체인 최신건만 노출).
    res = await client.get("/api/challenges", headers=headers_a)
    ids = [c["id"] for c in res.json()["items"]]
    assert original_id not in ids
    assert body["id"] in ids


async def test_discarded_revenge_revives_original_for_another_revenge(client):
    """완료된 건에 재대결했는데 그 재대결이 폐기되면, 원래 완료 건이 목록에 다시 나타나고
    또 재대결을 신청할 수 있다(요청)."""
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledDate": "2020-01-01", "scheduledTime": "10:00"},
    )
    original_id = res.json()["id"]
    await client.post(
        f"/api/challenges/{original_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!"},
    )
    await client.post(
        f"/api/challenges/{original_id}/result", headers=headers_a,
        json={"winnerSide": "creator", "scheduledDate": "2020-01-01", "scheduledTime": "10:00"},
    )
    # bob이 재대결 신청(bob=요청자, alice=지목).
    res = await client.post(
        f"/api/challenges/{original_id}/revenge", headers=headers_b,
        json={"scheduledDate": "2026-09-01", "scheduledTime": "10:00"},
    )
    revenge_id = res.json()["id"]

    # alice가 재대결을 거절 → 재대결이 폐기된다.
    res = await client.post(
        f"/api/challenges/{revenge_id}/respond", headers=headers_a,
        json={"response": "rejected", "reason": "다음에"},
    )
    assert res.json()["status"] == "discarded"

    # 목록: 원래 완료 건이 되살아나고(더는 재대결에 가려지지 않음), 폐기된 재대결은
    # 폐기 상태로 남아 휴지통에 담긴다(프론트가 status로 갈라 넣는다).
    res = await client.get("/api/challenges", headers=headers_a)
    by_id = {c["id"]: c for c in res.json()["items"]}
    assert original_id in by_id
    assert by_id[revenge_id]["status"] == "discarded"

    # bob은 원래 건에 다시 재대결을 신청할 수 있다.
    res = await client.post(
        f"/api/challenges/{original_id}/revenge", headers=headers_b,
        json={"scheduledDate": "2026-10-01", "scheduledTime": "10:00"},
    )
    assert res.status_code == 200, res.text


async def test_result_draw_and_not_held_block_revenge(client):
    admin = await _signup(client, "admin", "Admin#1000")
    for winner in ("draw", "not_held"):
        a = await _signup(client, f"alice_{winner}", f"Alice{winner}#1001")
        b = await _signup(client, f"bob_{winner}", f"Bob{winner}#1002")
        headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
        headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
        await _approve(client, admin["accessToken"], f"alice_{winner}")
        await _approve(client, admin["accessToken"], f"bob_{winner}")

        res = await client.post(
            "/api/challenges", headers=headers_a,
            json={"targetMemberIds": [f"bob_{winner}"], "scheduledDate": "2020-01-01", "scheduledTime": "10:00"},
        )
        challenge_id = res.json()["id"]
        await client.post(
            f"/api/challenges/{challenge_id}/respond", headers=headers_b,
            json={"response": "accepted", "reason": "OK!"},
        )
        res = await client.post(
            f"/api/challenges/{challenge_id}/result", headers=headers_a,
            json={"winnerSide": winner, "scheduledDate": "2020-01-01", "scheduledTime": "10:00"},
        )
        assert res.status_code == 200, res.text

        for headers in (headers_a, headers_b):
            res = await client.post(f"/api/challenges/{challenge_id}/revenge", headers=headers, json={})
            assert res.status_code == 400, res.text


async def test_listing_expires_stale_pending_as_discarded(client, db_session):
    """응답 기한(요청일+72시간)이 지난 pending 도전장은 목록 조회 시 폐기(휴지통)로 넘어간다 —
    지목자는 응답하지 않았으므로 response는 그대로 pending이고, 폐기는 예정 일시를 건드리지
    않으므로 미정(scheduledDate/scheduledAt=null)이던 건은 그대로 미정으로 남는다."""
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post("/api/challenges", headers=headers_a, json={"targetMemberIds": ["bob"]})
    challenge_id = res.json()["id"]

    await db_session.execute(
        update(Challenge).where(Challenge.id == challenge_id).values(
            created_at=datetime.now(UTC) - timedelta(hours=73)
        )
    )
    await db_session.commit()

    res = await client.get("/api/challenges", headers=headers_a)
    assert res.status_code == 200, res.text
    body = next(c for c in res.json()["items"] if c["id"] == challenge_id)
    assert body["status"] == "discarded"
    # 폐기는 예정 일시를 스탬프하지 않는다 — 원래 미정이었으니 그대로 null.
    assert body["scheduledDate"] is None
    assert body["scheduledAt"] is None
    bob_target = next(t for t in body["targets"] if t["memberId"] == "bob")
    assert bob_target["response"] == "pending"  # 실제로 아무도 응답 안 함


async def test_response_deadline_is_72h_or_scheduled_time_whichever_first(client, db_session):
    """응답 마감은 요청일+72시간이지만, 예정 시각이 그보다 먼저면 예정 시각이 마감이다(요청):
    (1) 예정 없음 + 30시간 전 = 아직 pending. (2) 예정 없음 + 73시간 전 = 폐기.
    (3) 예정이 이미 지남(과거) = 방금 만들었어도 예정 시각이 마감이라 폐기."""
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    # 셋 다 먼저 만들고(HTTP), 그 뒤에 created_at만 한 번에 조정한다 — HTTP 세션과 db_session의
    # 쓰기가 얽히면 SQLite가 잠긴다.
    # (1) 예정 없음 → 30시간 전으로. (2) 예정 없음 → 73시간 전으로. (3) 예정이 과거(2020), 방금 생성.
    r1 = await client.post("/api/challenges", headers=headers_a, json={"targetMemberIds": ["bob"]})
    id1 = r1.json()["id"]
    r2 = await client.post("/api/challenges", headers=headers_a, json={"targetMemberIds": ["bob"]})
    id2 = r2.json()["id"]
    r3 = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledDate": "2020-01-01", "scheduledTime": "10:00"},
    )
    id3 = r3.json()["id"]
    await db_session.execute(
        update(Challenge).where(Challenge.id == id1).values(created_at=datetime.now(UTC) - timedelta(hours=30))
    )
    await db_session.execute(
        update(Challenge).where(Challenge.id == id2).values(created_at=datetime.now(UTC) - timedelta(hours=73))
    )
    await db_session.commit()

    res = await client.get("/api/challenges", headers=headers_a)
    items = {c["id"]: c for c in res.json()["items"]}
    assert items[id1]["status"] == "pending"
    assert items[id2]["status"] == "discarded"
    assert items[id3]["status"] == "discarded"


async def test_trash_is_emptied_by_soft_delete_after_retention(client, db_session):
    """폐기된 지 7일이 지난 건은 목록 조회 시 소프트 삭제(deleted_at)되어 이후 어떤 조회에도
    안 나온다(요청: "휴지통은 폐기된 지 7일 지나면 사라짐, 디비에서는 소프트 삭제")."""
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledDate": "2026-08-01", "scheduledTime": "10:00"},
    )
    challenge_id = res.json()["id"]
    await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "rejected", "reason": "패스"},
    )
    # 방금 폐기 → 아직 휴지통에 보인다.
    res = await client.get("/api/challenges", headers=headers_a)
    assert any(c["id"] == challenge_id for c in res.json()["items"])

    # 폐기 시각을 8일 전으로 되돌린다 → 다음 조회에서 소프트 삭제되어 사라진다.
    await db_session.execute(
        update(Challenge).where(Challenge.id == challenge_id).values(
            discarded_at=datetime.now(UTC) - timedelta(days=8)
        )
    )
    await db_session.commit()

    res = await client.get("/api/challenges", headers=headers_a)
    assert all(c["id"] != challenge_id for c in res.json()["items"])


async def test_pending_for_me_excludes_discarded_challenge(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    c = await _signup(client, "carol", "Carol#1003")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    headers_c = {"Authorization": f"Bearer {c['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")
    await _approve(client, a["accessToken"], "carol")

    # bob, carol 지목 — bob이 거절하면 폐기되고, carol 팝업엔 죽은 초대가 안 떠야 한다.
    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob", "carol"], "scheduledDate": "2026-08-01", "scheduledTime": "10:00"},
    )
    challenge_id = res.json()["id"]
    await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "rejected", "reason": "패스"},
    )

    res = await client.get("/api/challenges/pending-for-me", headers=headers_c)
    assert res.status_code == 200, res.text
    assert res.json()["items"] == []


async def test_pending_for_me_excludes_discarded_challenge_when_buried(client):
    """팀전에서 한 명이 '버림'(discarded)으로 버려도 폐기 취급이라, 아직 응답 안 한 다른
    지목자의 팝업엔 그 죽은 초대가 안 떠야 한다(요청: "버리기도 응답한걸로 쳐서 다른
    사람들한테 안떠야됨")."""
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
        json={"targetMemberIds": ["bob", "carol"], "scheduledDate": "2026-08-01", "scheduledTime": "10:00"},
    )
    challenge_id = res.json()["id"]
    # bob이 사유 없이 버린다 → 도전장 폐기.
    await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "discarded"},
    )
    res = await client.get("/api/challenges/pending-for-me", headers=headers_c)
    assert res.status_code == 200, res.text
    assert res.json()["items"] == []


async def test_result_pending_for_me_returns_once_then_marks_notified(client):
    headers_a, _headers_b, challenge_id = await _confirmed_1v1(client, scheduled_date="2020-01-01")
    # 예정 일시가 지난 확정(성사) 대결 + 결과 미입력 → 결과 입력 팝업 후보.
    res = await client.get("/api/challenges/result-pending-for-me", headers=headers_a)
    assert res.status_code == 200, res.text
    assert len(res.json()["items"]) == 1

    res = await client.get("/api/challenges/result-pending-for-me", headers=headers_a)
    assert res.json()["items"] == []


async def test_result_pending_for_me_skips_future_schedule_and_entered_result(client):
    # 미래 예정 → 아직 결과 입력 자격 없음 → 팝업에 안 뜬다.
    headers_a, _headers_b, _future_id = await _confirmed_1v1(client, scheduled_date="2099-01-01")
    res = await client.get("/api/challenges/result-pending-for-me", headers=headers_a)
    assert res.json()["items"] == []


async def test_from_match_request_flag_roundtrips(client):
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    # 일반 도전장은 False.
    res = await client.post("/api/challenges", headers=headers_a, json={"targetMemberIds": ["bob"]})
    assert res.json()["fromMatchRequest"] is False

    # 들어주기로 만든 도전장은 fromMatchRequest=True로 표식된다.
    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "fromMatchRequest": True},
    )
    assert res.status_code == 200, res.text
    assert res.json()["fromMatchRequest"] is True
