"""리그(League/Tournament) 도메인 테스트 — CRUD, 로스터 중복/개인리그 제약, 빈 대진표
생성 후 슬롯 배정(부전승 정확성 — 특히 부전승 팀이 다음 라운드에서 실제 상대와 붙어야
하는 경우와, 실제 경기 결과가 나중에 들어오면서 그 반대편이 구조적으로 영원히 비어있어
자동 부전승이 연쇄되는 경우), 슬롯 오버라이드, 결과 입력+진출 전파, 대타 기록, 결과 취소,
비운영자 403."""

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


async def test_team_creation_labels_and_max_six_for_team_league(client):
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, mode="team")
    teams = await _add_teams(client, admin_headers, league["id"], 6)
    assert [t["label"] for t in teams] == list("ABCDEF")

    res = await client.post(f"/api/leagues/{league['id']}/teams", headers=admin_headers)
    assert res.status_code == 400, res.text


async def test_individual_league_allows_up_to_24(client):
    """개인리그는 "팀"이 곧 선수 1명이라 훨씬 많이 참가할 수 있어야 한다(요청: "개인전은
    최대 24명까지 가능하게")."""
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, mode="individual")
    teams = await _add_teams(client, admin_headers, league["id"], 24)
    assert len(teams) == 24
    assert teams[-1]["label"] == "X"  # 알파벳 24번째

    res = await client.post(f"/api/leagues/{league['id']}/teams", headers=admin_headers)
    assert res.status_code == 400, res.text


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

    # teamCount는 스키마 레벨에서 2~24만 허용.
    res = await _generate_bracket(client, admin_headers, league["id"], 1)
    assert res.status_code == 422, res.text
    res = await _generate_bracket(client, admin_headers, league["id"], 25)
    assert res.status_code == 422, res.text

    # 팀리그는 서비스 레벨에서 6개 상한.
    res = await _generate_bracket(client, admin_headers, league["id"], 7)
    assert res.status_code == 400, res.text

    # 이미 만들어진 팀(3개)보다 적게는 예약할 수 없다.
    res = await _generate_bracket(client, admin_headers, league["id"], 2)
    assert res.status_code == 400, res.text


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
    """3팀(A,B,C) → draw_size=4로 예약. A,B를 슬롯0에 배정하면 실제 경기라 자동 처리가
    없고, C를 슬롯1에 배정하면 반대쪽(리프 인덱스3)이 team_count=3 밖이라 그 즉시
    부전승 처리된다. 하지만 결승에서는 A-vs-B 승자와 실제로 붙어야 한다 — 자동으로
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

    await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[0]["id"])
    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "b", teams[1]["id"])
    assert res.status_code == 200, res.text
    slot0 = _match(res.json(), 1, 0)
    assert slot0["teamA"]["label"] == "A" and slot0["teamB"]["label"] == "B"
    assert slot0["winnerTeamId"] is None  # 실제 경기라 자동 처리 안 됨

    res = await _assign_slot(client, admin_headers, league["id"], slot1["id"], "a", teams[2]["id"])
    assert res.status_code == 200, res.text
    body = res.json()
    slot1 = _match(body, 1, 1)
    final = _match(body, 2, 0)
    assert slot1["winnerTeamId"] == teams[2]["id"]  # C 자동 부전승
    # 핵심 회귀 검증: 결승이 C의 부전승만으로 이미 끝나 있으면 안 된다.
    assert final["winnerTeamId"] is None
    assert final["teamB"]["label"] == "C"
    assert final["teamA"] is None  # 아직 A-vs-B 결과를 기다리는 중

    res = await _enter_result(client, admin_headers, league["id"], slot0["id"], 1, 0)  # A 승
    assert res.status_code == 200, res.text
    body = res.json()
    final = _match(body, 2, 0)
    assert final["teamA"]["label"] == "A" and final["teamB"]["label"] == "C"
    assert final["winnerTeamId"] is None  # 둘 다 실제 팀이라 진짜 경기가 필요하다

    res = await _enter_result(client, admin_headers, league["id"], final["id"], 1, 0)  # A 우승
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "completed"


async def test_six_team_bracket_late_real_result_triggers_downstream_bye(client):
    """6팀 예약(draw_size=8). 슬롯3(리프 6,7)은 team_count=6 밖이라 처음부터 is_dead다.
    라운드2 슬롯1은 (라운드1 슬롯2=E vs F 실제 경기) vs (라운드1 슬롯3=완전공백)이라,
    E-vs-F 결과가 나중에 들어오는 순간 그 즉시 라운드2도 자동으로 부전승 처리돼야 한다."""
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
    assert slots[3]["isDead"] and slots[3]["teamA"] is None and slots[3]["teamB"] is None
    r2_0, r2_1 = _match(body, 2, 0), _match(body, 2, 1)
    assert not r2_0["isDead"] and not r2_1["isDead"]

    for slot_idx, ta, tb in [(0, teams[0], teams[1]), (1, teams[2], teams[3]), (2, teams[4], teams[5])]:
        await _assign_slot(client, admin_headers, league["id"], slots[slot_idx]["id"], "a", ta["id"])
        res = await _assign_slot(client, admin_headers, league["id"], slots[slot_idx]["id"], "b", tb["id"])
        assert res.status_code == 200, res.text
    body = res.json()
    ab, cd, ef = _match(body, 1, 0), _match(body, 1, 1), _match(body, 1, 2)
    assert ab["winnerTeamId"] is None and cd["winnerTeamId"] is None and ef["winnerTeamId"] is None

    res = await _enter_result(client, admin_headers, league["id"], ef["id"], 1, 0)  # E 승
    assert res.status_code == 200, res.text
    body = res.json()
    r2_1 = _match(body, 2, 1)
    # E-vs-F 결과가 들어오자마자, 반대편이 영원히 안 채워지는 걸 알고 있으므로 즉시
    # 부전승 처리돼 다음 라운드(결승)까지 자동 진출해야 한다.
    assert r2_1["winnerTeamId"] == teams[4]["id"]  # E
    assert r2_1["setsWonA"] is None  # 실제로 치른 경기가 아니라 자동 부전승

    res = await _enter_result(client, admin_headers, league["id"], ab["id"], 1, 0)  # A 승
    assert res.status_code == 200
    res = await _enter_result(client, admin_headers, league["id"], cd["id"], 1, 0)  # C 승
    assert res.status_code == 200
    body = res.json()
    r2_0 = _match(body, 2, 0)
    assert r2_0["teamA"]["label"] == "A" and r2_0["teamB"]["label"] == "C"
    assert r2_0["winnerTeamId"] is None  # 실제 경기 필요

    res = await _enter_result(client, admin_headers, league["id"], r2_0["id"], 1, 0)  # A 승
    assert res.status_code == 200, res.text
    body = res.json()
    final = _match(body, 3, 0)
    assert final["teamA"]["label"] == "A" and final["teamB"]["label"] == "E"
    assert body["status"] == "active"

    res = await _enter_result(client, admin_headers, league["id"], final["id"], 1, 0)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "completed"


async def test_slot_override_and_round_conflict(client):
    admin_headers, members = await _bootstrap(client, 4)
    league = await _create_league(client, admin_headers)
    teams = await _add_teams(client, admin_headers, league["id"], 4)
    for t, mid in zip(teams, [f"m{i}" for i in range(4)]):
        assert (await _set_roster(client, admin_headers, league["id"], t["id"], [mid])).status_code == 200
    res = await _generate_bracket(client, admin_headers, league["id"], 4)
    body = res.json()
    slot0, slot1 = _match(body, 1, 0), _match(body, 1, 1)

    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[0]["id"])
    assert res.status_code == 200, res.text

    # 이미 슬롯0에 배정된 팀(teams[0]=A)을 슬롯1에도 넣으려 하면 충돌.
    res = await _assign_slot(client, admin_headers, league["id"], slot1["id"], "a", teams[0]["id"])
    assert res.status_code == 409, res.text

    # 슬롯 비우기는 허용.
    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", None)
    assert res.status_code == 200, res.text
    assert _match(res.json(), 1, 0)["teamA"] is None


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
