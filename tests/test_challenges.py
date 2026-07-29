"""도전장("너 나와!") 게시판 스모크 테스트 — 상태 4개(응답대기/성사/완료/폐기)뿐인 구조.
취소/연기/재신청/설욕전(재대결)은 모두 제거됐고, 거절·무응답·미실시는 폐기(휴지통)로
통합됐다. 도전장끼리 이어 붙는 연계 개념은 이제 없다 — 한 건은 그 자체로 끝난다."""

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
    client, *, scheduled_date: str = "2020-01-01",
) -> tuple[dict, dict, int]:
    """alice(요청자)↔bob 1:1 확정(성사) 대결 하나를 만들어 (headers_a, headers_b, id) 반환."""
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")
    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledDate": scheduled_date},
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
        json={"targetMemberIds": ["bob"], "scheduledDate": "2026-08-01"},
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
        json={"targetMemberIds": ["bob", "carol"], "scheduledDate": "2026-08-01"},
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
        json={"targetMemberIds": ["bob", "carol"], "scheduledDate": "2026-08-01"},
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
        json={"targetMemberIds": ["bob"], "scheduledDate": "2026-08-01"},
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
        json={"targetMemberIds": ["bob"], "scheduledDate": "2026-08-01"},
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
        json={"winnerSide": "creator", "scheduledDate": "2026-08-01"},
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
        json={"response": "accepted", "reason": "OK!", "scheduledDate": "2026-09-01"},
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
        json={"targetMemberIds": ["bob"], "scheduledDate": "2026-08-01"},
    )
    challenge_id = res.json()["id"]

    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!", "scheduledDate": "2099-01-01"},
    )
    # 요청자가 정한 시간을 응답자가 바꿀 수 없다 — 원래 값 유지.
    assert res.json()["scheduledDate"] == "2026-08-01"


async def test_accepting_date_only_challenge_lets_target_add_time_note(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    # 요청자가 날짜만 정하고 "언제"는 비워서 보낸다.
    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledDate": "2026-08-01"},
    )
    challenge_id = res.json()["id"]
    assert res.json()["scheduledDate"] == "2026-08-01"

    # 응답자는 날짜는 못 바꾸지만(요청자가 정한 날짜 유지) "언제"는 덧붙일 수 있다(요청).
    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={
            "response": "accepted", "reason": "OK!",
            "scheduledDate": "2099-12-31", "scheduledTimeNote": "퇴근하고",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["scheduledDate"] == "2026-08-01"
    assert body["scheduledTimeNote"] == "퇴근하고"


async def test_enter_result_blocked_before_confirmed(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledDate": "2026-08-01"},
    )
    challenge_id = res.json()["id"]

    # 아직 성사(confirmed) 전이라 결과를 넣을 수 없다 — 유효한 페이로드를 다 보내도 400(비즈니스 규칙).
    res = await client.post(
        f"/api/challenges/{challenge_id}/result", headers=headers_a,
        json={"winnerSide": "creator", "scheduledDate": "2026-08-01"},
    )
    assert res.status_code == 400, res.text  # 아직 pending

    await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!"},
    )
    # 성사된 뒤엔 예정 일시가 안 지났어도 결과를 바로 입력할 수 있다(예전의 "일시 지남" 제약 제거).
    res = await client.post(
        f"/api/challenges/{challenge_id}/result", headers=headers_a,
        json={"winnerSide": "creator", "scheduledDate": "2026-08-01"},
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
        json={"targetMemberIds": ["bob"], "scheduledDate": "2020-01-01"},
    )
    challenge_id = res.json()["id"]
    await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "reason": "OK!"},
    )

    res = await client.post(
        f"/api/challenges/{challenge_id}/result", headers=headers_c,
        json={"winnerSide": "creator", "scheduledDate": "2020-01-01"},
    )
    assert res.status_code == 403, res.text  # 참가자 아님

    res = await client.post(
        f"/api/challenges/{challenge_id}/result", headers=headers_b,
        json={"winnerSide": "target", "scheduledDate": "2020-01-01"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "done"
    assert res.json()["resultWinnerSide"] == "target"

    res = await client.post(
        f"/api/challenges/{challenge_id}/result", headers=headers_a,
        json={"winnerSide": "creator", "scheduledDate": "2020-01-01"},
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
        json={"winnerSide": "not_held", "scheduledDate": "2020-01-01"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "discarded"


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
        json={"targetMemberIds": ["bob"], "scheduledDate": "2020-01-01"},
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
        json={"targetMemberIds": ["bob"], "scheduledDate": "2026-08-01"},
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
        json={"targetMemberIds": ["bob", "carol"], "scheduledDate": "2026-08-01"},
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
        json={"targetMemberIds": ["bob", "carol"], "scheduledDate": "2026-08-01"},
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


async def test_time_note_is_free_text_and_never_affects_schedule(client):
    """약속 시간을 사람 말로 적어 두는 자리(요청: 시간 필드 대신 "언제"를 한마디처럼).

    저장/조회만 되고 날짜(정렬·마감 기준)에는 아무 영향이 없어야 한다.
    """
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={
            "targetMemberIds": ["bob"],
            "scheduledDate": "2026-08-01",
            "scheduledTimeNote": "그날 봐서",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    challenge_id = body["id"]
    assert body["scheduledDate"] == "2026-08-01"
    assert body["scheduledTimeNote"] == "그날 봐서"
    # 시간 미정과 같은 취급이라 파생 일시는 그날 0시(KST) = 전날 15:00Z 그대로다.
    assert body["scheduledAt"] == "2026-07-31T15:00:00Z"

    # 요청자가 이미 적어 뒀으면 응답자가 덮어쓸 수 없다.
    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "scheduledTimeNote": "아무도 몰래"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["scheduledTimeNote"] == "그날 봐서"


async def test_target_can_add_time_note_when_creator_left_it_blank(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    headers_b = {"Authorization": f"Bearer {b['accessToken']}"}
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledDate": "2026-08-01"},
    )
    challenge_id = res.json()["id"]
    assert res.json()["scheduledTimeNote"] == ""

    res = await client.post(
        f"/api/challenges/{challenge_id}/respond", headers=headers_b,
        json={"response": "accepted", "scheduledTimeNote": "퇴근하고"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["scheduledTimeNote"] == "퇴근하고"
    # 날짜는 요청자가 정한 그대로.
    assert res.json()["scheduledDate"] == "2026-08-01"


async def test_time_note_kept_without_date(client):
    """날짜와 "언제"는 서로 상관없이 따로 적는다(요청: "둘은 이제 상관없이 별도로 입력가능").

    예전에는 날짜가 없으면 "언제" 메모를 버렸는데, 그러면 사람이 적어 넣은 말이 소리 없이
    사라졌다. 날짜 없이 "아무도 몰래"만 적어 보내는 것도 이제 그대로 남는다.
    """
    a = await _signup(client, "alice", "Alice#1001")
    headers_a = {"Authorization": f"Bearer {a['accessToken']}"}
    await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges", headers=headers_a,
        json={"targetMemberIds": ["bob"], "scheduledTimeNote": "아무도 몰래"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["scheduledDate"] is None
    assert res.json()["scheduledTimeNote"] == "아무도 몰래"

    # 수락하는 쪽도 날짜 없이 "언제"만 덧붙일 수 있다.
    res = await client.post(
        "/api/challenges", headers=headers_a, json={"targetMemberIds": ["bob"]},
    )
    cid = res.json()["id"]
    b_token = (await client.post("/api/auth/login", json={"id": "bob", "password": "pass1234"})).json()
    res = await client.post(
        f"/api/challenges/{cid}/respond",
        headers={"Authorization": f"Bearer {b_token['accessToken']}"},
        json={"response": "accepted", "scheduledTimeNote": "그날 봐서"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["scheduledDate"] is None
    assert res.json()["scheduledTimeNote"] == "그날 봐서"
