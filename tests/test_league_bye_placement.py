"""부전승 자리를 관리자가 고른다(요청).

같은 6팀 8강 대진이라도 부전승을 어디에 두느냐로 대진 모양이 갈린다.

  앞쪽 두 칸(기본)   : 두 팀이 4강 직행 + 네 팀이 8강
  뒤쪽 두 칸(요청)   : 네 팀 토너먼트 + 두 팀 단판, 그 둘이 결승

요청받은 건 뒤쪽이고, 그건 자료구조를 바꾸지 않고 부전승 자리만 옮기면 나오는 모양이다.
"""

from tests.test_leagues import (
    _add_teams,
    _assign_slot,
    _bootstrap,
    _create_league,
    _generate_bracket,
    _get_league,
    _match,
)


async def _set_byes(client, headers, league_id: int, slots: list[dict]):
    return await client.put(
        f"/api/leagues/{league_id}/bracket/byes", headers=headers, json={"slots": slots},
    )


async def _six_team_league(client):
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 6)  # A~F
    res = await _generate_bracket(client, admin_headers, league["id"], 6)
    assert res.status_code == 200, res.text
    return admin_headers, league["id"], teams, res.json()


async def test_default_byes_sit_on_the_front_slots(client):
    """기본값은 예전과 같다 — 앞쪽 두 칸의 b자리가 영구 공백이다."""
    _, _, _, body = await _six_team_league(client)
    assert body["drawSize"] == 8
    assert [_match(body, 1, s)["byeSide"] for s in range(4)] == ["b", "b", None, None]


async def test_admin_moves_byes_to_build_a_compound_bracket(client):
    """부전승을 뒤쪽 두 칸으로 옮기면 요청한 복합 구조가 나온다.

    R1  (A vs B) (C vs D) (E vs -) (F vs -)
    R2      (A/B 승 vs C/D 승)        (E vs F)      ← 왼쪽=4팀 토너먼트, 오른쪽=두 팀 단판
    R3                  (결   승)
    """
    headers, lid, teams, body = await _six_team_league(client)

    # 부전승을 슬롯 2·3의 b자리로 옮긴다.
    res = await _set_byes(client, headers, lid, [
        {"matchId": _match(body, 1, 2)["id"], "side": "b"},
        {"matchId": _match(body, 1, 3)["id"], "side": "b"},
    ])
    assert res.status_code == 200, res.text
    body = res.json()
    assert [_match(body, 1, s)["byeSide"] for s in range(4)] == [None, None, "b", "b"]

    # 여섯 팀을 A~F 순서로 앉힌다 — 앞 두 칸은 양쪽 다, 뒤 두 칸은 a자리만.
    for slot, side, team in (
        (0, "a", teams[0]), (0, "b", teams[1]),
        (1, "a", teams[2]), (1, "b", teams[3]),
        (2, "a", teams[4]), (3, "a", teams[5]),
    ):
        cur = await _get_league(client, headers, lid)
        res = await _assign_slot(client, headers, lid, _match(cur, 1, slot)["id"], side, team["id"])
        assert res.status_code == 200, res.text

    body = await _get_league(client, headers, lid)
    # 앞 두 칸은 실제 경기라 아직 승자가 없다.
    assert _match(body, 1, 0)["winnerTeamId"] is None
    assert _match(body, 1, 1)["winnerTeamId"] is None
    # 뒤 두 칸은 부전승이라 E·F가 바로 올라간다.
    assert _match(body, 1, 2)["winnerTeamId"] == teams[4]["id"]
    assert _match(body, 1, 3)["winnerTeamId"] == teams[5]["id"]
    # 그 둘이 2라운드 한 칸에서 만난다 — 이게 "다른 두 명의 대결"이다.
    r2b = _match(body, 2, 1)
    assert {r2b["teamA"]["label"], r2b["teamB"]["label"]} == {"E", "F"}
    # 2라운드 다른 칸(4팀 토너먼트의 결승)은 아직 비어 있다.
    r2a = _match(body, 2, 0)
    assert r2a["teamA"] is None and r2a["teamB"] is None


async def test_moving_byes_undoes_the_old_advancement(client):
    """부전승 자리를 옮기면 그 자리에서 이미 올라갔던 결정도 함께 되돌린다."""
    headers, lid, teams, body = await _six_team_league(client)

    # 기본 배치(슬롯 0,1이 부전승)에서 A를 슬롯0 a자리에 앉히면 바로 올라간다.
    res = await _assign_slot(client, headers, lid, _match(body, 1, 0)["id"], "a", teams[0]["id"])
    assert res.status_code == 200, res.text
    body = res.json()
    assert _match(body, 1, 0)["winnerTeamId"] == teams[0]["id"]
    assert _match(body, 2, 0)["teamA"]["label"] == "A"

    # 부전승을 뒤로 옮기면 그 진출이 취소된다 — A는 이제 실제 경기를 치러야 한다.
    res = await _set_byes(client, headers, lid, [
        {"matchId": _match(body, 1, 2)["id"], "side": "b"},
        {"matchId": _match(body, 1, 3)["id"], "side": "b"},
    ])
    assert res.status_code == 200, res.text
    body = res.json()
    assert _match(body, 1, 0)["winnerTeamId"] is None
    assert _match(body, 1, 0)["teamA"]["label"] == "A"  # 배정 자체는 남는다
    assert _match(body, 2, 0)["teamA"] is None


async def test_bye_count_must_match_and_round1_only(client):
    """개수가 안 맞거나 2라운드 자리를 주면 거부한다."""
    headers, lid, _, body = await _six_team_league(client)

    res = await _set_byes(client, headers, lid, [{"matchId": _match(body, 1, 2)["id"], "side": "b"}])
    assert res.status_code == 400, res.text
    assert "2개" in res.text

    res = await _set_byes(client, headers, lid, [
        {"matchId": _match(body, 2, 0)["id"], "side": "b"},
        {"matchId": _match(body, 1, 3)["id"], "side": "b"},
    ])
    assert res.status_code == 400, res.text
    assert "1라운드" in res.text

    # 한 경기에 둘은 안 된다.
    mid = _match(body, 1, 2)["id"]
    res = await _set_byes(client, headers, lid, [
        {"matchId": mid, "side": "a"}, {"matchId": mid, "side": "b"},
    ])
    assert res.status_code == 400, res.text


async def test_team_cannot_be_seeded_onto_a_bye_side(client):
    """영구 공백 자리에는 팀을 앉힐 수 없다 — 앉혀 봐야 영원히 안 붙는다."""
    headers, lid, teams, body = await _six_team_league(client)
    # 기본 배치에서 슬롯0의 b가 부전승 자리다.
    res = await _assign_slot(client, headers, lid, _match(body, 1, 0)["id"], "b", teams[0]["id"])
    assert res.status_code == 400, res.text
    assert "부전승 자리" in res.text


async def test_byes_locked_after_bracket_confirm(client):
    """대진 확정 뒤에는 부전승 자리도 못 바꾼다 — 대진 모양 자체를 다시 짜는 일이라서."""
    headers, lid, _, body = await _six_team_league(client)
    res = await client.post(f"/api/leagues/{lid}/bracket/confirm", headers=headers)
    assert res.status_code == 200, res.text

    res = await _set_byes(client, headers, lid, [
        {"matchId": _match(body, 1, 2)["id"], "side": "b"},
        {"matchId": _match(body, 1, 3)["id"], "side": "b"},
    ])
    assert res.status_code == 409, res.text


async def test_regenerate_keeps_seeded_pairs_and_puts_byes_on_free_slots(client):
    """규모를 다시 잡을 때 부전승은 '이미 두 팀이 다 찬 칸'을 피해서 깔린다.

    기존 약속(요청: "참가팀수 늘릴 때 기존 지정된 건 리셋하지 말아줘")을 지키려면 다 찬
    칸에 부전승을 얹어 한 팀을 쫓아낼 수가 없다.
    """
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, best_of=1)
    lid = league["id"]
    teams = await _add_teams(client, admin_headers, lid, 4)

    res = await _generate_bracket(client, admin_headers, lid, 4)
    assert res.status_code == 200, res.text
    body = res.json()
    assert all(_match(body, 1, s)["byeSide"] is None for s in range(2))  # 4팀은 부전승 없음

    # 슬롯0을 A vs B로 꽉 채운다.
    for side, team in (("a", teams[0]), ("b", teams[1])):
        cur = await _get_league(client, admin_headers, lid)
        res = await _assign_slot(client, admin_headers, lid, _match(cur, 1, 0)["id"], side, team["id"])
        assert res.status_code == 200, res.text

    # 6팀으로 늘리면 부전승 2개가 필요한데, 슬롯0은 다 차 있으므로 1·2·3 중에서 고른다.
    res = await _generate_bracket(client, admin_headers, lid, 6)
    assert res.status_code == 200, res.text
    body = res.json()
    assert _match(body, 1, 0)["byeSide"] is None
    assert _match(body, 1, 0)["teamA"]["label"] == "A"
    assert _match(body, 1, 0)["teamB"]["label"] == "B"
    assert sum(1 for s in range(4) if _match(body, 1, s)["byeSide"]) == 2
