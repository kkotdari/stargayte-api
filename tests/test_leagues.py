"""리그(League/Tournament) 도메인 테스트 — CRUD, 로스터 중복/개인리그 제약, 빈 대진표
생성 후 슬롯 배정(부전승 정확성 — 특히 부전승 팀이 다음 라운드에서 실제 상대와 붙어야
하는 경우와, 실제 경기 결과가 나중에 들어오면서 그 반대편이 구조적으로 영원히 비어있어
자동 부전승이 연쇄되는 경우), 슬롯 오버라이드, 결과 입력+진출 전파, 대타 기록, 결과 취소,
비운영자 403."""

import string

from sqlalchemy import select

from app.domain.leagues.models import League, LeagueTeam


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


async def _bootstrap(client, n: int) -> tuple[dict, list[dict]]:
    """admin(첫 가입자, 자동 운영자+active) 헤더와 n명의 승인된 일반 회원 헤더 목록."""
    admin = await _signup(client, "admin", "Admin#0001")
    admin_headers = {"Authorization": f"Bearer {admin['accessToken']}"}
    members = []
    for i in range(n):
        mid = f"m{i}"
        m = await _signup(client, mid, f"M{i}#100{i}")
        await _approve(client, admin["accessToken"], mid)
        members.append({"Authorization": f"Bearer {m['accessToken']}"})
    return admin_headers, members


async def _create_league(client, headers, *, name="리그", mode="team", best_of=3) -> dict:
    res = await client.post(
        "/api/leagues", headers=headers,
        json={"name": name, "mode": mode, "bestOf": best_of},
    )
    assert res.status_code == 200, res.text
    return res.json()


async def _add_team(client, headers, league_id: int) -> dict:
    res = await client.post(f"/api/leagues/{league_id}/teams", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


async def _add_teams(client, headers, league_id: int, n: int) -> list[dict]:
    return [await _add_team(client, headers, league_id) for _ in range(n)]


async def _set_roster(client, headers, league_id: int, team_id: int, member_ids: list[str]):
    return await client.put(
        f"/api/leagues/{league_id}/teams/{team_id}/roster",
        headers=headers, json={"memberIds": member_ids},
    )


async def _generate_bracket(client, headers, league_id: int, team_count: int):
    return await client.post(
        f"/api/leagues/{league_id}/bracket/generate", headers=headers, json={"teamCount": team_count},
    )


async def _assign_slot(client, headers, league_id: int, match_id: int, side: str, team_id: int | None):
    return await client.patch(
        f"/api/leagues/{league_id}/matches/{match_id}/slot",
        headers=headers, json={"side": side, "teamId": team_id},
    )


async def _confirm_bracket(client, headers, league_id: int):
    return await client.post(f"/api/leagues/{league_id}/bracket/confirm", headers=headers)


def _match(league: dict, round_: int, slot: int) -> dict:
    m = next(m for m in league["matches"] if m["round"] == round_ and m["slotInRound"] == slot)
    return m


async def _enter_result(client, headers, league_id: int, match_id: int, a: int, b: int, substitutes=None):
    return await client.post(
        f"/api/leagues/{league_id}/matches/{match_id}/result",
        headers=headers, json={"setsWonA": a, "setsWonB": b, "substitutes": substitutes or []},
    )


async def test_non_admin_forbidden(client):
    admin_headers, members = await _bootstrap(client, 1)
    res = await client.get("/api/leagues", headers=members[0])
    assert res.status_code == 403, res.text
    res = await client.post("/api/leagues", headers=members[0], json={"name": "x", "mode": "team"})
    assert res.status_code == 403, res.text


async def test_create_get_update_delete_league(client):
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, name="가을리그", best_of=3)
    assert league["status"] == "setup"
    assert league["mode"] == "team"
    assert league["drawSize"] is None

    res = await client.get(f"/api/leagues/{league['id']}", headers=admin_headers)
    assert res.status_code == 200
    assert res.json()["name"] == "가을리그"

    res = await client.patch(
        f"/api/leagues/{league['id']}", headers=admin_headers, json={"name": "겨울리그"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["name"] == "겨울리그"

    res = await client.delete(f"/api/leagues/{league['id']}", headers=admin_headers)
    assert res.status_code == 204
    res = await client.get(f"/api/leagues/{league['id']}", headers=admin_headers)
    assert res.status_code == 404


async def test_team_creation_labels_unlimited_and_multichar_after_26(client):
    """팀/선수 수는 상한이 없다(요청: "팀수 무제한 개인전 선수 무제한 대진표 슬롯
    무제한"). 26개(Z)를 넘어가면 라벨이 스프레드시트 열 이름 방식(AA, AB..)으로
    이어진다."""
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, mode="team")
    teams = await _add_teams(client, admin_headers, league["id"], 28)
    assert [t["label"] for t in teams[:26]] == list(string.ascii_uppercase)
    assert teams[26]["label"] == "AA"
    assert teams[27]["label"] == "AB"


async def test_individual_league_allows_more_than_24(client):
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, mode="individual")
    teams = await _add_teams(client, admin_headers, league["id"], 30)
    assert len(teams) == 30
    assert teams[29]["label"] == "AD"


async def test_team_delete_relabels_remaining(client):
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers)
    teams = await _add_teams(client, admin_headers, league["id"], 3)  # A, B, C
    res = await client.delete(f"/api/leagues/{league['id']}/teams/{teams[0]['id']}", headers=admin_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert [t["label"] for t in body["teams"]] == ["A", "B"]


async def test_roster_rejects_cross_team_duplicate_and_bad_count(client):
    admin_headers, members = await _bootstrap(client, 3)
    league = await _create_league(client, admin_headers)
    teams = await _add_teams(client, admin_headers, league["id"], 2)

    res = await _set_roster(client, admin_headers, league["id"], teams[0]["id"], ["m0", "m1"])
    assert res.status_code == 200, res.text

    # m0은 이미 팀A 소속 — 팀B에 다시 넣으면 409.
    res = await _set_roster(client, admin_headers, league["id"], teams[1]["id"], ["m0", "m2"])
    assert res.status_code == 409, res.text

    # 같은 팀 안에서 같은 회원 두 번 — 스키마 검증(FastAPI 기본 422)에서 걸린다.
    res = await _set_roster(client, admin_headers, league["id"], teams[1]["id"], ["m2", "m2"])
    assert res.status_code == 422, res.text


async def test_individual_league_roster_locked_to_one_and_no_substitutes(client):
    admin_headers, members = await _bootstrap(client, 2)
    league = await _create_league(client, admin_headers, mode="individual", best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 2)

    res = await _set_roster(client, admin_headers, league["id"], teams[0]["id"], ["m0", "m1"])
    assert res.status_code == 400, res.text  # 개인리그는 1명만

    res = await _set_roster(client, admin_headers, league["id"], teams[0]["id"], ["m0"])
    assert res.status_code == 200, res.text
    res = await _set_roster(client, admin_headers, league["id"], teams[1]["id"], ["m1"])
    assert res.status_code == 200, res.text

    res = await _generate_bracket(client, admin_headers, league["id"], 2)
    assert res.status_code == 200, res.text
    slot0 = _match(res.json(), 1, 0)
    await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[0]["id"])
    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "b", teams[1]["id"])
    final = _match(res.json(), 1, 0)

    res = await _enter_result(
        client, admin_headers, league["id"], final["id"], 1, 0,
        substitutes=[{"teamId": teams[0]["id"], "rosterPosition": 0, "substituteMemberId": "m1", "note": ""}],
    )
    assert res.status_code == 400, res.text  # 개인리그는 대타 불가


async def test_bracket_generate_team_count_validation(client):
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers)
    await _add_teams(client, admin_headers, league["id"], 3)

    # teamCount는 스키마 레벨에서 2 이상만 강제한다 — 상한은 없다(요청: "대진표 슬롯
    # 무제한").
    res = await _generate_bracket(client, admin_headers, league["id"], 1)
    assert res.status_code == 422, res.text

    # 이미 만들어진 팀(3개)보다 적게는 예약할 수 없다.
    res = await _generate_bracket(client, admin_headers, league["id"], 2)
    assert res.status_code == 400, res.text

    # 상한 없이 큰 규모도 허용된다(팀리그라도 더는 6개로 막지 않는다).
    res = await _generate_bracket(client, admin_headers, league["id"], 25)
    assert res.status_code == 200, res.text


async def test_generate_bracket_can_be_resized_before_results_but_not_after(client):
    """팀수/대진표 슬롯 수는 대진표 생성 후에도 다시 잡을 수 있다(요청: "팀수, 대진표
    슬롯 수 다 수정가능해야돼") — 단, 실제 경기 결과가 하나라도 들어갔으면 재생성이
    그 진행 상황을 지워버리므로 막는다."""
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 2)

    res = await _generate_bracket(client, admin_headers, league["id"], 2)
    assert res.status_code == 200, res.text
    assert res.json()["drawSize"] == 2

    res = await _generate_bracket(client, admin_headers, league["id"], 4)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["drawSize"] == 4
    assert body["plannedTeams"] == 4

    slot0 = _match(body, 1, 0)
    await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[0]["id"])
    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "b", teams[1]["id"])
    assert res.status_code == 200, res.text
    res = await _enter_result(client, admin_headers, league["id"], slot0["id"], 1, 0)
    assert res.status_code == 200, res.text

    res = await _generate_bracket(client, admin_headers, league["id"], 8)
    assert res.status_code == 400, res.text


async def test_generate_bracket_resize_preserves_round1_assignments(client):
    """참가팀수를 늘려 규모를 다시 잡아도 이미 1라운드에 배정해둔 팀은 그대로 남는다
    (요청: "참가팀수 늘릴때 기존 지정된건 리셋하지 말아줘") — 결과가 없으니 2라운드
    이상은 구조가 바뀌는 김에 새로 만들어져도 손실이 없다."""
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 4)  # A, B, C, D

    res = await _generate_bracket(client, admin_headers, league["id"], 4)
    assert res.status_code == 200, res.text
    body = res.json()
    slot0 = _match(body, 1, 0)
    await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[0]["id"])
    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "b", teams[1]["id"])
    assert res.status_code == 200, res.text

    # 4 -> 6팀으로 늘리면 draw_size가 4에서 8로 커진다 — 슬롯0(A vs B)은 그대로 남고,
    # 새로 생긴 슬롯(2,3)만 빈 채로 추가된다.
    res = await _generate_bracket(client, admin_headers, league["id"], 6)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["drawSize"] == 8
    slot0 = _match(body, 1, 0)
    assert slot0["teamA"]["label"] == "A" and slot0["teamB"]["label"] == "B"
    assert _match(body, 1, 2)["teamA"] is None and _match(body, 1, 3)["teamA"] is None


async def test_bracket_generates_empty_and_slots_are_assigned_manually(client):
    """대진표는 팀이 있건 없건, 있어도 자동으로 채워 넣지 않고 항상 빈 채로 생성된다 —
    각 칸에 누가 들어갈지는 슬롯 API로 직접 정한다(요청: "대진표 생성 누르면 빈 대진표가
    생기고 각 칸에 누가 들어갈지 정할 수 있는 시스템으로")."""
    admin_headers, members = await _bootstrap(client, 2)
    league = await _create_league(client, admin_headers, best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 2)
    for t, mid in zip(teams, ["m0", "m1"]):
        assert (await _set_roster(client, admin_headers, league["id"], t["id"], [mid])).status_code == 200

    res = await _generate_bracket(client, admin_headers, league["id"], 4)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["drawSize"] == 4
    assert body["plannedTeams"] == 4
    # 팀이 이미 2개 있었어도 자동으로 안 채워지고 전부 비어 있어야 한다.
    for m in body["matches"]:
        assert m["teamA"] is None and m["teamB"] is None
        if m["round"] == 1:
            assert not m["isDead"]  # 2팀뿐이지만 4자리를 예약했으니 전부 아직 살아있음

    slot0 = _match(body, 1, 0)
    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[0]["id"])
    assert res.status_code == 200, res.text
    assert _match(res.json(), 1, 0)["teamA"]["label"] == "A"

    # 죽은(is_dead) 슬롯에는 배정할 수 없다 — 슬롯1(리프 2,3)은 team_count=4 안이라
    # 살아있고, 4강 나머지는 이 테스트에서 안 다루지만 최소 확인: 이미 결과가 난 경기엔
    # 배정 불가(다른 테스트에서 커버) / 존재하지 않는 매치 404 등은 기존 커버리지로 충분.


async def test_add_team_capped_at_planned_teams_after_generate(client):
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers)
    await _add_teams(client, admin_headers, league["id"], 2)
    res = await _generate_bracket(client, admin_headers, league["id"], 3)
    assert res.status_code == 200, res.text

    res = await client.post(f"/api/leagues/{league['id']}/teams", headers=admin_headers)
    assert res.status_code == 200, res.text  # 3번째 팀 — 예약된 자리라 허용

    res = await client.post(f"/api/leagues/{league['id']}/teams", headers=admin_headers)
    assert res.status_code == 400, res.text  # 4번째 — 예약(3) 초과라 거부


async def test_three_team_bracket_bye_must_still_play_next_round(client):
    """3팀(A,B,C) → draw_size=4로 예약, byes=1이라 슬롯0이 부전승 자리(분산 배정 —
    "각 부전승을 팀별로 분산 배정")다. C를 슬롯0에 혼자 배정하면 반대쪽 자리가
    구조적으로 영원히 안 채워지므로 그 즉시 부전승 처리된다. A,B는 슬롯1에 실제
    경기로 배정한다. 하지만 결승에서는 A-vs-B 승자와 C가 실제로 붙어야 한다 — 자동으로
    우승 처리되면 안 된다(수정한 버그)."""
    admin_headers, members = await _bootstrap(client, 3)
    league = await _create_league(client, admin_headers, best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 3)  # A, B, C
    for t, mid in zip(teams, ["m0", "m1", "m2"]):
        assert (await _set_roster(client, admin_headers, league["id"], t["id"], [mid])).status_code == 200

    res = await _generate_bracket(client, admin_headers, league["id"], 3)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["drawSize"] == 4
    slot0, slot1 = _match(body, 1, 0), _match(body, 1, 1)
    assert not slot0["isDead"] and not slot1["isDead"]

    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[2]["id"])
    assert res.status_code == 200, res.text
    body = res.json()
    slot0 = _match(body, 1, 0)
    final = _match(body, 2, 0)
    assert slot0["winnerTeamId"] == teams[2]["id"]  # C 자동 부전승(슬롯0 = 부전승 자리)
    # 핵심 회귀 검증: 결승이 C의 부전승만으로 이미 끝나 있으면 안 된다.
    assert final["winnerTeamId"] is None
    assert final["teamA"]["label"] == "C"
    assert final["teamB"] is None  # 아직 A-vs-B 결과를 기다리는 중

    await _assign_slot(client, admin_headers, league["id"], slot1["id"], "a", teams[0]["id"])
    res = await _assign_slot(client, admin_headers, league["id"], slot1["id"], "b", teams[1]["id"])
    assert res.status_code == 200, res.text
    slot1 = _match(res.json(), 1, 1)
    assert slot1["teamA"]["label"] == "A" and slot1["teamB"]["label"] == "B"
    assert slot1["winnerTeamId"] is None  # 실제 경기라 자동 처리 안 됨

    res = await _enter_result(client, admin_headers, league["id"], slot1["id"], 1, 0)  # A 승
    assert res.status_code == 200, res.text
    body = res.json()
    final = _match(body, 2, 0)
    assert final["teamA"]["label"] == "C" and final["teamB"]["label"] == "A"
    assert final["winnerTeamId"] is None  # 둘 다 실제 팀이라 진짜 경기가 필요하다

    res = await _enter_result(client, admin_headers, league["id"], final["id"], 1, 0)  # C 우승
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "completed"


async def test_six_team_bracket_byes_spread_and_round2_is_real_match(client):
    """6팀 예약(draw_size=8, byes=2)이면 부전승 두 자리가 한 칸에 몰리지 않고 슬롯0,1에
    하나씩 분산 배정된다("각 부전승을 팀별로 분산 배정") — 슬롯2,3은 완전히 실제 경기가
    필요한 자리다. 어느 라운드1 매치도 완전히 죽지(is_dead) 않는다. 두 부전승 팀(A,B)이
    라운드2에서 만나면 그건 자동 진출이 아니라 진짜 경기여야 하고, 마찬가지로
    (C-vs-D 승자) vs (E-vs-F 승자)도 진짜 경기다 — 라운드2는 항상 4팀이 실제로 채워지는
    정상적인 준결승으로 기능해야 한다(부전승 팀이 결승까지 자동 진출해버리면 안 됨)."""
    admin_headers, members = await _bootstrap(client, 6)
    league = await _create_league(client, admin_headers, best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 6)  # A..F
    for t, mid in zip(teams, [f"m{i}" for i in range(6)]):
        assert (await _set_roster(client, admin_headers, league["id"], t["id"], [mid])).status_code == 200

    res = await _generate_bracket(client, admin_headers, league["id"], 6)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["drawSize"] == 8
    slots = {i: _match(body, 1, i) for i in range(4)}
    for i in range(4):
        assert not slots[i]["isDead"]  # 부전승이 분산 배정되니 완전히 죽는 슬롯이 없다
    r2_0, r2_1 = _match(body, 2, 0), _match(body, 2, 1)
    assert not r2_0["isDead"] and not r2_1["isDead"]

    # 슬롯0,1은 부전승 자리(A, B가 각각 혼자 배정돼 즉시 진출) — 슬롯2,3은 실제 경기.
    res = await _assign_slot(client, admin_headers, league["id"], slots[0]["id"], "a", teams[0]["id"])
    assert res.status_code == 200, res.text
    a_bye = _match(res.json(), 1, 0)
    assert a_bye["winnerTeamId"] == teams[0]["id"]  # A 자동 부전승

    res = await _assign_slot(client, admin_headers, league["id"], slots[1]["id"], "a", teams[1]["id"])
    assert res.status_code == 200, res.text
    b_bye = _match(res.json(), 1, 1)
    assert b_bye["winnerTeamId"] == teams[1]["id"]  # B 자동 부전승

    for slot_idx, ta, tb in [(2, teams[2], teams[3]), (3, teams[4], teams[5])]:
        await _assign_slot(client, admin_headers, league["id"], slots[slot_idx]["id"], "a", ta["id"])
        res = await _assign_slot(client, admin_headers, league["id"], slots[slot_idx]["id"], "b", tb["id"])
        assert res.status_code == 200, res.text
    body = res.json()
    cd, ef = _match(body, 1, 2), _match(body, 1, 3)
    assert cd["winnerTeamId"] is None and ef["winnerTeamId"] is None  # 실제 경기라 자동 처리 안 됨

    r2_0 = _match(body, 2, 0)
    assert r2_0["teamA"]["label"] == "A" and r2_0["teamB"]["label"] == "B"
    assert r2_0["winnerTeamId"] is None  # 둘 다 부전승으로 왔어도 라운드2는 진짜 경기가 필요

    res = await _enter_result(client, admin_headers, league["id"], r2_0["id"], 1, 0)  # A 승
    assert res.status_code == 200, res.text

    res = await _enter_result(client, admin_headers, league["id"], cd["id"], 1, 0)  # C 승
    assert res.status_code == 200
    res = await _enter_result(client, admin_headers, league["id"], ef["id"], 1, 0)  # E 승
    assert res.status_code == 200
    body = res.json()
    r2_1 = _match(body, 2, 1)
    assert r2_1["teamA"]["label"] == "C" and r2_1["teamB"]["label"] == "E"
    assert r2_1["winnerTeamId"] is None  # 실제 경기 필요

    res = await _enter_result(client, admin_headers, league["id"], r2_1["id"], 1, 0)  # C 승
    assert res.status_code == 200, res.text
    body = res.json()
    final = _match(body, 3, 0)
    assert final["teamA"]["label"] == "A" and final["teamB"]["label"] == "C"
    assert body["status"] == "active"

    res = await _enter_result(client, admin_headers, league["id"], final["id"], 1, 0)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "completed"


async def test_slot_reassign_moves_team_and_blocks_after_decided(client):
    admin_headers, members = await _bootstrap(client, 4)
    league = await _create_league(client, admin_headers, best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 4)
    for t, mid in zip(teams, [f"m{i}" for i in range(4)]):
        assert (await _set_roster(client, admin_headers, league["id"], t["id"], [mid])).status_code == 200
    res = await _generate_bracket(client, admin_headers, league["id"], 4)
    body = res.json()
    slot0, slot1 = _match(body, 1, 0), _match(body, 1, 1)

    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[0]["id"])
    assert res.status_code == 200, res.text

    # 이미 슬롯0에 배정된 팀(teams[0]=A)을 슬롯1에 다시 배정하면 슬롯0에서 자동으로
    # 빠지고 슬롯1로 옮겨간다(요청: "이미 지정된 팀도 드롭다운에 나오고 새로 지정하면
    # 기존 지정된 슬롯을 미지정으로 지우는 식").
    res = await _assign_slot(client, admin_headers, league["id"], slot1["id"], "a", teams[0]["id"])
    assert res.status_code == 200, res.text
    body = res.json()
    assert _match(body, 1, 0)["teamA"] is None
    assert _match(body, 1, 1)["teamA"]["label"] == "A"

    # 슬롯 비우기는 허용.
    res = await _assign_slot(client, admin_headers, league["id"], slot1["id"], "a", None)
    assert res.status_code == 200, res.text
    assert _match(res.json(), 1, 1)["teamA"] is None

    # 이미 결과가 정해진 경기에 배정된 팀은 다른 자리로 옮길 수 없다.
    await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[0]["id"])
    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "b", teams[1]["id"])
    assert res.status_code == 200, res.text
    res = await _enter_result(client, admin_headers, league["id"], slot0["id"], 1, 0)
    assert res.status_code == 200, res.text

    res = await _assign_slot(client, admin_headers, league["id"], slot1["id"], "a", teams[0]["id"])
    assert res.status_code == 409, res.text


async def test_bye_seed_reassignable_before_confirm_and_cascade_clears_propagation(client):
    """대진 확정 전에는 부전승으로 이미 결정된 자리도 자유롭게 다시 배정할 수 있고
    (요청: "그전엔 부전승팀도 수정 가능해야해"), 그 결정이 이미 다음 라운드로
    전파돼 있었다면 슬롯을 바꾸는 순간 그 전파도 함께 취소된다."""
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 3)  # A, B, C
    res = await _generate_bracket(client, admin_headers, league["id"], 3)
    body = res.json()
    slot0 = _match(body, 1, 0)  # byes=1이라 슬롯0이 부전승 자리

    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[0]["id"])
    assert res.status_code == 200, res.text
    body = res.json()
    assert _match(body, 1, 0)["winnerTeamId"] == teams[0]["id"]  # A 자동 부전승
    final = _match(body, 2, 0)
    assert final["teamA"]["label"] == "A"  # 결승까지 전파돼 있음

    # A 대신 C를 그 부전승 자리에 다시 배정하면, A의 부전승 결정과 결승으로의 전파가
    # 함께 취소되고 C가 새로 부전승을 받는다.
    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[2]["id"])
    assert res.status_code == 200, res.text
    body = res.json()
    slot0 = _match(body, 1, 0)
    assert slot0["teamA"]["label"] == "C" and slot0["winnerTeamId"] == teams[2]["id"]
    final = _match(body, 2, 0)
    assert final["teamA"]["label"] == "C"
    assert final["winnerTeamId"] is None


async def test_confirm_bracket_locks_seed_changes(client):
    """대진 확정 버튼을 누르면 그 뒤로는 시드(슬롯) 변경 자체가 막힌다(요청: "대진
    확정 버튼을 추가해주고 그걸 누르면 그때부터 시드는 변경 못하게")."""
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 2)
    res = await _generate_bracket(client, admin_headers, league["id"], 2)
    body = res.json()
    assert body["bracketLocked"] is False
    slot0 = _match(body, 1, 0)

    res = await _confirm_bracket(client, admin_headers, league["id"])
    assert res.status_code == 200, res.text
    assert res.json()["bracketLocked"] is True

    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[0]["id"])
    assert res.status_code == 409, res.text

    res = await _generate_bracket(client, admin_headers, league["id"], 4)
    assert res.status_code == 409, res.text  # 규모 변경도 확정 후엔 막힌다


async def test_result_set_score_validation_against_best_of(client):
    admin_headers, members = await _bootstrap(client, 2)
    league = await _create_league(client, admin_headers, best_of=3)  # 2세트 선취
    teams = await _add_teams(client, admin_headers, league["id"], 2)
    for t, mid in zip(teams, ["m0", "m1"]):
        assert (await _set_roster(client, admin_headers, league["id"], t["id"], [mid])).status_code == 200
    res = await _generate_bracket(client, admin_headers, league["id"], 2)
    slot0 = _match(res.json(), 1, 0)
    await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[0]["id"])
    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "b", teams[1]["id"])
    match = _match(res.json(), 1, 0)

    res = await _enter_result(client, admin_headers, league["id"], match["id"], 1, 1)  # 동점
    assert res.status_code == 400, res.text
    res = await _enter_result(client, admin_headers, league["id"], match["id"], 2, 0)  # 정상(3전2승)
    assert res.status_code == 200, res.text
    res = await _enter_result(client, admin_headers, league["id"], match["id"], 2, 1)  # 이미 결과 있음
    assert res.status_code == 409, res.text


async def test_substitute_is_recorded_for_team_league(client):
    admin_headers, members = await _bootstrap(client, 4)
    league = await _create_league(client, admin_headers, best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 2)
    res = await _set_roster(client, admin_headers, league["id"], teams[0]["id"], ["m0", "m1"])
    assert res.status_code == 200
    res = await _set_roster(client, admin_headers, league["id"], teams[1]["id"], ["m2", "m3"])
    assert res.status_code == 200
    res = await _generate_bracket(client, admin_headers, league["id"], 2)
    slot0 = _match(res.json(), 1, 0)
    await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[0]["id"])
    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "b", teams[1]["id"])
    match = _match(res.json(), 1, 0)

    res = await _enter_result(
        client, admin_headers, league["id"], match["id"], 1, 0,
        substitutes=[{"teamId": teams[0]["id"], "rosterPosition": 1, "substituteMemberId": "m2", "note": "부상"}],
    )
    assert res.status_code == 200, res.text
    body = res.json()
    m = _match(body, 1, 0)
    assert len(m["substitutions"]) == 1
    assert m["substitutions"][0]["substituteMemberId"] == "m2"
    assert m["substitutions"][0]["note"] == "부상"


async def test_clear_result_cascades_downstream(client):
    admin_headers, members = await _bootstrap(client, 4)
    league = await _create_league(client, admin_headers, best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 4)
    for t, mid in zip(teams, [f"m{i}" for i in range(4)]):
        assert (await _set_roster(client, admin_headers, league["id"], t["id"], [mid])).status_code == 200
    res = await _generate_bracket(client, admin_headers, league["id"], 4)
    body = res.json()
    slot0, slot1 = _match(body, 1, 0), _match(body, 1, 1)
    await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[0]["id"])
    await _assign_slot(client, admin_headers, league["id"], slot0["id"], "b", teams[1]["id"])
    await _assign_slot(client, admin_headers, league["id"], slot1["id"], "a", teams[2]["id"])
    res = await _assign_slot(client, admin_headers, league["id"], slot1["id"], "b", teams[3]["id"])
    body = res.json()
    ab, cd = _match(body, 1, 0), _match(body, 1, 1)

    res = await _enter_result(client, admin_headers, league["id"], ab["id"], 1, 0)
    res = await _enter_result(client, admin_headers, league["id"], cd["id"], 1, 0)
    body = res.json()
    final = _match(body, 2, 0)
    res = await _enter_result(client, admin_headers, league["id"], final["id"], 1, 0)
    assert res.status_code == 200
    assert res.json()["status"] == "completed"

    res = await client.delete(
        f"/api/leagues/{league['id']}/matches/{ab['id']}/result", headers=admin_headers
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # ab 결과가 취소되면 결승의 teamA도 다시 비고, 결승 자체의 결과도 같이 취소돼야 한다.
    final = _match(body, 2, 0)
    ab_after = _match(body, 1, 0)
    assert ab_after["winnerTeamId"] is None
    assert final["teamA"] is None
    assert final["winnerTeamId"] is None
    assert body["status"] == "active"


async def test_delete_league_cascades(client, db_session):
    admin_headers, members = await _bootstrap(client, 2)
    league = await _create_league(client, admin_headers)
    teams = await _add_teams(client, admin_headers, league["id"], 2)
    await _set_roster(client, admin_headers, league["id"], teams[0]["id"], ["m0"])

    res = await client.delete(f"/api/leagues/{league['id']}", headers=admin_headers)
    assert res.status_code == 204

    remaining_leagues = (await db_session.execute(select(League))).scalars().all()
    remaining_teams = (await db_session.execute(select(LeagueTeam))).scalars().all()
    assert remaining_leagues == []
    assert remaining_teams == []  # 로스터가 있던 팀도 리그 삭제로 같이 지워져야 한다


async def _seed(client, headers, league_id: int, assignments: list[dict]):
    return await client.put(
        f"/api/leagues/{league_id}/bracket/seeding",
        headers=headers, json={"assignments": assignments},
    )


async def test_bracket_seeding_batch_atomic_swap(client):
    """1라운드 시드를 한 번에 저장하고, 두 팀을 맞바꾸는 편집도 원자적으로 반영되는지 —
    자리별 순차 저장이면 '팀 이동' 자동비움이 이미 넣은 자리를 덮어써 깨지던 케이스."""
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, mode="team")
    teams = await _add_teams(client, admin_headers, league["id"], 4)  # A, B, C, D
    res = await _generate_bracket(client, admin_headers, league["id"], 4)
    assert res.status_code == 200, res.text
    lg = res.json()
    m0, m1 = _match(lg, 1, 0), _match(lg, 1, 1)

    # 최초 시드: m0=(A,B), m1=(C,D)
    res = await _seed(client, admin_headers, league["id"], [
        {"matchId": m0["id"], "side": "a", "teamId": teams[0]["id"]},
        {"matchId": m0["id"], "side": "b", "teamId": teams[1]["id"]},
        {"matchId": m1["id"], "side": "a", "teamId": teams[2]["id"]},
        {"matchId": m1["id"], "side": "b", "teamId": teams[3]["id"]},
    ])
    assert res.status_code == 200, res.text
    lg = res.json()
    assert _match(lg, 1, 0)["teamA"]["id"] == teams[0]["id"]
    assert _match(lg, 1, 0)["teamB"]["id"] == teams[1]["id"]
    assert _match(lg, 1, 1)["teamA"]["id"] == teams[2]["id"]
    assert _match(lg, 1, 1)["teamB"]["id"] == teams[3]["id"]

    # A <-> C 스왑: m0.a=C, m1.a=A (나머지 그대로). 최종 상태가 정확히 반영돼야 한다.
    res = await _seed(client, admin_headers, league["id"], [
        {"matchId": m0["id"], "side": "a", "teamId": teams[2]["id"]},
        {"matchId": m0["id"], "side": "b", "teamId": teams[1]["id"]},
        {"matchId": m1["id"], "side": "a", "teamId": teams[0]["id"]},
        {"matchId": m1["id"], "side": "b", "teamId": teams[3]["id"]},
    ])
    assert res.status_code == 200, res.text
    lg = res.json()
    assert _match(lg, 1, 0)["teamA"]["id"] == teams[2]["id"]
    assert _match(lg, 1, 0)["teamB"]["id"] == teams[1]["id"]
    assert _match(lg, 1, 1)["teamA"]["id"] == teams[0]["id"]
    assert _match(lg, 1, 1)["teamB"]["id"] == teams[3]["id"]


async def test_bracket_seeding_batch_resolves_bye(client):
    """일괄 저장에서도 부전승 자동 처리가 마지막에 한 번 돌아 다음 라운드로 전파되는지."""
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, mode="team")
    teams = await _add_teams(client, admin_headers, league["id"], 3)  # A, B, C
    res = await _generate_bracket(client, admin_headers, league["id"], 3)
    assert res.status_code == 200, res.text
    lg = res.json()
    # draw_size=4, byes=1 → slot0은 부전 자리(side a만 실제팀), slot1은 실제 경기.
    m0, m1 = _match(lg, 1, 0), _match(lg, 1, 1)
    res = await _seed(client, admin_headers, league["id"], [
        {"matchId": m0["id"], "side": "a", "teamId": teams[0]["id"]},
        {"matchId": m1["id"], "side": "a", "teamId": teams[1]["id"]},
        {"matchId": m1["id"], "side": "b", "teamId": teams[2]["id"]},
    ])
    assert res.status_code == 200, res.text
    lg = res.json()
    # A는 부전승으로 결승 진출.
    assert _match(lg, 1, 0)["winnerTeamId"] == teams[0]["id"]
    assert _match(lg, 2, 0)["teamA"]["id"] == teams[0]["id"]


async def test_bracket_seeding_rejects_duplicate_team_and_locked(client):
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, mode="team")
    teams = await _add_teams(client, admin_headers, league["id"], 4)
    res = await _generate_bracket(client, admin_headers, league["id"], 4)
    lg = res.json()
    m0, m1 = _match(lg, 1, 0), _match(lg, 1, 1)

    # 같은 팀을 두 자리에 → 거부.
    res = await _seed(client, admin_headers, league["id"], [
        {"matchId": m0["id"], "side": "a", "teamId": teams[0]["id"]},
        {"matchId": m1["id"], "side": "a", "teamId": teams[0]["id"]},
    ])
    assert res.status_code == 400, res.text

    # 정상 저장 후 대진 확정 → 그 뒤 일괄 저장은 잠겨서 거부.
    res = await _seed(client, admin_headers, league["id"], [
        {"matchId": m0["id"], "side": "a", "teamId": teams[0]["id"]},
        {"matchId": m0["id"], "side": "b", "teamId": teams[1]["id"]},
        {"matchId": m1["id"], "side": "a", "teamId": teams[2]["id"]},
        {"matchId": m1["id"], "side": "b", "teamId": teams[3]["id"]},
    ])
    assert res.status_code == 200, res.text
    await _confirm_bracket(client, admin_headers, league["id"])
    res = await _seed(client, admin_headers, league["id"], [
        {"matchId": m0["id"], "side": "a", "teamId": teams[1]["id"]},
    ])
    assert res.status_code == 409, res.text
