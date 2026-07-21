"""리그(League/Tournament) 도메인 테스트 — CRUD, 로스터 중복/개인리그 제약, 대진표
생성(부전승 정확성 — 특히 부전승 팀이 다음 라운드에서 실제 상대와 붙어야 하는 경우와,
실제 경기 결과가 나중에 들어오면서 그 반대편이 구조적으로 영원히 비어있어 자동 부전승이
연쇄되는 경우), 슬롯 오버라이드, 결과 입력+진출 전파, 대타 기록, 결과 취소, 비운영자 403."""

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


async def test_team_creation_labels_and_max_six(client):
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers)
    teams = await _add_teams(client, admin_headers, league["id"], 6)
    assert [t["label"] for t in teams] == list("ABCDEF")

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
    league = await _create_league(client, admin_headers, mode="individual")
    teams = await _add_teams(client, admin_headers, league["id"], 2)

    res = await _set_roster(client, admin_headers, league["id"], teams[0]["id"], ["m0", "m1"])
    assert res.status_code == 400, res.text  # 개인리그는 1명만

    res = await _set_roster(client, admin_headers, league["id"], teams[0]["id"], ["m0"])
    assert res.status_code == 200, res.text
    res = await _set_roster(client, admin_headers, league["id"], teams[1]["id"], ["m1"])
    assert res.status_code == 200, res.text

    res = await client.post(f"/api/leagues/{league['id']}/bracket/generate", headers=admin_headers)
    assert res.status_code == 200, res.text
    league_body = res.json()
    final = _match(league_body, 1, 0)

    res = await _enter_result(
        client, admin_headers, league["id"], final["id"], 2, 0,
        substitutes=[{"teamId": teams[0]["id"], "rosterPosition": 0, "substituteMemberId": "m1", "note": ""}],
    )
    assert res.status_code == 400, res.text  # 개인리그는 대타 불가


async def test_bracket_generate_requires_two_teams(client):
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers)
    await _add_team(client, admin_headers, league["id"])
    res = await client.post(f"/api/leagues/{league['id']}/bracket/generate", headers=admin_headers)
    assert res.status_code == 400, res.text


async def test_three_team_bracket_bye_must_still_play_next_round(client):
    """3팀(A,B,C) → draw_size=4. C는 라운드1에서 부전승으로 올라가지만, 결승(라운드2)에서는
    A-vs-B 승자와 실제로 붙어야 한다 — 자동으로 우승 처리되면 안 된다(수정한 버그)."""
    admin_headers, members = await _bootstrap(client, 3)
    league = await _create_league(client, admin_headers, best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 3)  # A, B, C
    for t, mid in zip(teams, ["m0", "m1", "m2"]):
        assert (await _set_roster(client, admin_headers, league["id"], t["id"], [mid])).status_code == 200

    res = await client.post(f"/api/leagues/{league['id']}/bracket/generate", headers=admin_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["drawSize"] == 4

    r1_real = _match(body, 1, 0)  # A vs B
    r1_bye = _match(body, 1, 1)  # C vs bye
    final = _match(body, 2, 0)
    assert r1_real["teamA"]["label"] == "A" and r1_real["teamB"]["label"] == "B"
    assert r1_real["winnerTeamId"] is None and not r1_real["isDead"]
    assert r1_bye["teamB"] is None and r1_bye["winnerTeamId"] == teams[2]["id"]  # C 부전승
    assert not r1_bye["isDead"]
    # 핵심 회귀 검증: 결승이 C의 부전승만으로 이미 끝나 있으면 안 된다.
    assert final["winnerTeamId"] is None
    assert final["teamB"]["label"] == "C"
    assert final["teamA"] is None  # 아직 A-vs-B 결과를 기다리는 중

    res = await _enter_result(client, admin_headers, league["id"], r1_real["id"], 1, 0)  # A 승
    assert res.status_code == 200, res.text
    body = res.json()
    final = _match(body, 2, 0)
    assert final["teamA"]["label"] == "A" and final["teamB"]["label"] == "C"
    assert final["winnerTeamId"] is None  # 둘 다 실제 팀이라 진짜 경기가 필요하다

    res = await _enter_result(client, admin_headers, league["id"], final["id"], 1, 0)  # A 우승
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "completed"


async def test_six_team_bracket_late_real_result_triggers_downstream_bye(client):
    """6팀 → draw_size=8. 라운드1 슬롯3(7,8번째 자리)은 완전 공백(is_dead). 라운드2
    슬롯1은 (라운드1 슬롯2=E vs F 실제 경기) vs (라운드1 슬롯3=완전공백)이라, E-vs-F
    결과가 나중에 들어오는 순간 그 즉시 라운드2도 자동으로 부전승 처리돼야 한다."""
    admin_headers, members = await _bootstrap(client, 6)
    league = await _create_league(client, admin_headers, best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 6)  # A..F
    for t, mid in zip(teams, [f"m{i}" for i in range(6)]):
        assert (await _set_roster(client, admin_headers, league["id"], t["id"], [mid])).status_code == 200

    res = await client.post(f"/api/leagues/{league['id']}/bracket/generate", headers=admin_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["drawSize"] == 8

    ab = _match(body, 1, 0)   # A vs B
    cd = _match(body, 1, 1)   # C vs D
    ef = _match(body, 1, 2)   # E vs F
    dead_slot = _match(body, 1, 3)  # 완전 공백
    assert dead_slot["isDead"] and dead_slot["teamA"] is None and dead_slot["teamB"] is None
    r2_0 = _match(body, 2, 0)  # AB승자 vs CD승자
    r2_1 = _match(body, 2, 1)  # EF승자 vs (죽은 슬롯)
    assert not r2_0["isDead"] and not r2_1["isDead"]
    assert r2_0["teamA"] is None and r2_0["teamB"] is None
    assert r2_1["teamA"] is None and r2_1["teamB"] is None

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
    res = await client.post(f"/api/leagues/{league['id']}/bracket/generate", headers=admin_headers)
    body = res.json()
    r1s1 = _match(body, 1, 1)

    # 이미 라운드1 슬롯0에 배정된 팀(teams[0]=A)을 슬롯1에도 넣으려 하면 충돌.
    res = await client.patch(
        f"/api/leagues/{league['id']}/matches/{r1s1['id']}/slot",
        headers=admin_headers, json={"side": "a", "teamId": teams[0]["id"]},
    )
    assert res.status_code == 409, res.text

    # 슬롯 비우기는 허용.
    res = await client.patch(
        f"/api/leagues/{league['id']}/matches/{r1s1['id']}/slot",
        headers=admin_headers, json={"side": "a", "teamId": None},
    )
    assert res.status_code == 200, res.text
    assert res.json()["teamA"] is None


async def test_result_set_score_validation_against_best_of(client):
    admin_headers, members = await _bootstrap(client, 2)
    league = await _create_league(client, admin_headers, best_of=3)  # 2세트 선취
    teams = await _add_teams(client, admin_headers, league["id"], 2)
    for t, mid in zip(teams, ["m0", "m1"]):
        assert (await _set_roster(client, admin_headers, league["id"], t["id"], [mid])).status_code == 200
    res = await client.post(f"/api/leagues/{league['id']}/bracket/generate", headers=admin_headers)
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
    res = await client.post(f"/api/leagues/{league['id']}/bracket/generate", headers=admin_headers)
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
    res = await client.post(f"/api/leagues/{league['id']}/bracket/generate", headers=admin_headers)
    body = res.json()
    ab, cd = _match(body, 1, 0), _match(body, 1, 1)

    res = await _enter_result(client, admin_headers, league["id"], ab["id"], 1, 0)
    body = res.json()
    res = await _enter_result(client, admin_headers, league["id"], cd["id"], 1, 0)
    body = res.json()
    final = _match(body, 2, 0)
    res = await _enter_result(client, admin_headers, league["id"], final["id"], 1, 0)
    assert res.status_code == 200
    assert res.json()["status"] == "completed"

    # 부전승은 취소 불가 — 실제 세트 스코어가 있는 결과만 취소 가능.
    bye_free_final_id = final["id"]
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
