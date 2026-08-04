"""어느 라운드의 칸에나 팀을 앉히고, 확정할 때 안 쓰는 가지를 죽인다(요청).

    "1라운드뿐 아니라 모든 칸에 대진을 넣을 수 있게 하고 확정을 눌렀을 때 필요없는 칸은
     지워지는 거야. 그래서 어떤 가지는 3라운드부터 경기가 있고 그 가지에 붙은 1, 2라운드는
     사라지는 거야(자연히 부전승도 되겠지)"

이 규칙 하나가 예전의 '부전승 자리 지정'을 대신한다 — 부전승은 따로 찍는 것이 아니라
'한 사람만 있는 가지'의 결과다.
"""

from tests.test_leagues import (
    _add_teams,
    _bootstrap,
    _confirm_bracket,
    _create_league,
    _generate_bracket,
    _get_league,
    _match,
    _seed,
)


async def _league_with_bracket(client, *, teams: int, rounds_for: int):
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, best_of=1)
    made = await _add_teams(client, admin_headers, league["id"], teams)
    res = await _generate_bracket(client, admin_headers, league["id"], rounds_for)
    assert res.status_code == 200, res.text
    return admin_headers, league["id"], made, res.json()


async def test_compound_bracket_from_seeding_a_round2_cell(client):
    """요청한 복합 구조 — 네 팀 토너먼트 + 두 팀 단판, 그 둘이 결승.

    2라운드 칸에 E·F를 바로 앉히면, 그 아래 1라운드 두 칸은 아무도 안 앉은 가지라
    확정할 때 죽는다. 부전승을 따로 찍지 않아도 이 모양이 그대로 나온다.
    """
    headers, lid, teams, body = await _league_with_bracket(client, teams=6, rounds_for=8)
    assert body["drawSize"] == 8
    r1 = {i: _match(body, 1, i) for i in range(4)}
    r2 = {i: _match(body, 2, i) for i in range(2)}

    res = await _seed(client, headers, lid, [
        # 왼쪽 절반 — 네 팀 토너먼트
        {"matchId": r1[0]["id"], "side": "a", "teamId": teams[0]["id"]},
        {"matchId": r1[0]["id"], "side": "b", "teamId": teams[1]["id"]},
        {"matchId": r1[1]["id"], "side": "a", "teamId": teams[2]["id"]},
        {"matchId": r1[1]["id"], "side": "b", "teamId": teams[3]["id"]},
        # 오른쪽 절반 — 두 팀이 2라운드에서 바로 붙는다
        {"matchId": r2[1]["id"], "side": "a", "teamId": teams[4]["id"]},
        {"matchId": r2[1]["id"], "side": "b", "teamId": teams[5]["id"]},
    ])
    assert res.status_code == 200, res.text

    res = await _confirm_bracket(client, headers, lid)
    assert res.status_code == 200, res.text
    body = res.json()

    # 오른쪽 절반의 1라운드 두 칸은 사라진다(표시상 죽는다).
    assert _match(body, 1, 2)["isDead"] is True
    assert _match(body, 1, 3)["isDead"] is True
    # 왼쪽 절반은 살아 있고 아직 아무도 안 올라갔다 — 진짜 경기다.
    assert _match(body, 1, 0)["isDead"] is False
    assert _match(body, 1, 0)["winnerTeamId"] is None
    # 2라운드 오른쪽은 E vs F 그대로, 왼쪽은 아직 비어 있다.
    r2b = _match(body, 2, 1)
    assert {r2b["teamA"]["label"], r2b["teamB"]["label"]} == {"E", "F"}
    assert r2b["winnerTeamId"] is None
    r2a = _match(body, 2, 0)
    assert r2a["teamA"] is None and r2a["teamB"] is None and r2a["isDead"] is False


async def test_team_seeded_in_round3_skips_two_rounds(client):
    """3라운드 칸에 바로 앉은 팀은 1·2라운드를 통째로 건너뛴다(요청의 그 예)."""
    headers, lid, teams, body = await _league_with_bracket(client, teams=4, rounds_for=8)
    r1 = {i: _match(body, 1, i) for i in range(4)}
    r3 = _match(body, 3, 0)

    res = await _seed(client, headers, lid, [
        {"matchId": r1[0]["id"], "side": "a", "teamId": teams[0]["id"]},
        {"matchId": r1[0]["id"], "side": "b", "teamId": teams[1]["id"]},
        {"matchId": r1[1]["id"], "side": "a", "teamId": teams[2]["id"]},
        # D는 결승 자리에 바로 앉는다 — 오른쪽 절반 전체가 D 하나짜리 가지가 된다.
        {"matchId": r3["id"], "side": "b", "teamId": teams[3]["id"]},
    ])
    assert res.status_code == 200, res.text
    res = await _confirm_bracket(client, headers, lid)
    assert res.status_code == 200, res.text
    body = res.json()

    # 오른쪽 절반은 통째로 죽는다.
    assert _match(body, 1, 2)["isDead"] and _match(body, 1, 3)["isDead"]
    assert _match(body, 2, 1)["isDead"]
    # C는 혼자라 2라운드로 부전승, 결승에는 D가 앉아 있다.
    assert _match(body, 1, 1)["winnerTeamId"] == teams[2]["id"]
    final = _match(body, 3, 0)
    assert final["teamB"]["label"] == "D"
    assert final["winnerTeamId"] is None   # A/B·C 쪽 결과를 기다린다


async def test_seeding_rejects_two_teams_on_one_branch(client):
    """한 가지에 씨앗이 둘이면 거부한다(요청: 거부) — 누가 올라갈지 모순이라서."""
    headers, lid, teams, body = await _league_with_bracket(client, teams=4, rounds_for=8)
    r1_0 = _match(body, 1, 0)
    r2_0 = _match(body, 2, 0)   # r1_0의 승자가 올라갈 자리

    res = await _seed(client, headers, lid, [
        {"matchId": r1_0["id"], "side": "a", "teamId": teams[0]["id"]},
        {"matchId": r2_0["id"], "side": "a", "teamId": teams[1]["id"]},
    ])
    assert res.status_code == 400, res.text
    assert "아래 경기" in res.text

    # 세 라운드 떨어진 조상도 마찬가지다.
    r3_0 = _match(body, 3, 0)
    res = await _seed(client, headers, lid, [
        {"matchId": r1_0["id"], "side": "a", "teamId": teams[0]["id"]},
        {"matchId": r3_0["id"], "side": "a", "teamId": teams[1]["id"]},
    ])
    assert res.status_code == 400, res.text

    # 다른 가지면 괜찮다.
    r2_1 = _match(body, 2, 1)
    res = await _seed(client, headers, lid, [
        {"matchId": r1_0["id"], "side": "a", "teamId": teams[0]["id"]},
        {"matchId": r2_1["id"], "side": "a", "teamId": teams[1]["id"]},
    ])
    assert res.status_code == 200, res.text


async def test_confirm_marks_dead_but_keeps_the_rows(client):
    """확정은 표시만 한다(요청: 표시만) — 원본을 남겨 두면 나중에 되돌릴 길이 있다."""
    headers, lid, teams, body = await _league_with_bracket(client, teams=2, rounds_for=4)
    before = len(body["matches"])
    r2_0 = _match(body, 2, 0)

    res = await _seed(client, headers, lid, [
        {"matchId": r2_0["id"], "side": "a", "teamId": teams[0]["id"]},
        {"matchId": r2_0["id"], "side": "b", "teamId": teams[1]["id"]},
    ])
    assert res.status_code == 200, res.text
    res = await _confirm_bracket(client, headers, lid)
    assert res.status_code == 200, res.text
    body = res.json()

    assert len(body["matches"]) == before          # 한 줄도 안 지웠다
    assert sum(1 for m in body["matches"] if m["isDead"]) > 0
    assert _match(body, 2, 0)["isDead"] is False   # 앉은 칸은 살아 있다


async def test_regenerate_keeps_seeds_that_still_fit(client):
    """판을 다시 잡아도 새 판에 있는 칸의 배정은 그대로 남는다."""
    headers, lid, teams, body = await _league_with_bracket(client, teams=4, rounds_for=8)
    r1_0 = _match(body, 1, 0)
    res = await _seed(client, headers, lid, [
        {"matchId": r1_0["id"], "side": "a", "teamId": teams[0]["id"]},
        {"matchId": r1_0["id"], "side": "b", "teamId": teams[1]["id"]},
    ])
    assert res.status_code == 200, res.text

    # 8칸 → 16칸으로 키운다.
    res = await client.post(
        f"/api/leagues/{lid}/bracket/generate", headers=headers, json={"rounds": 4},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["drawSize"] == 16
    kept = _match(body, 1, 0)
    assert kept["teamA"]["label"] == "A" and kept["teamB"]["label"] == "B"

    # 다시 4칸으로 줄여도 (1,0)은 새 판에도 있는 칸이라 남는다.
    res = await client.post(
        f"/api/leagues/{lid}/bracket/generate", headers=headers, json={"rounds": 2},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["drawSize"] == 4
    kept = _match(body, 1, 0)
    assert kept["teamA"]["label"] == "A" and kept["teamB"]["label"] == "B"


async def test_confirm_is_blocked_when_nobody_is_seeded(client):
    """아무도 안 앉은 판은 확정할 수 없다 — 굳힐 것이 없으면 판이 통째로 죽는다."""
    headers, lid, _teams, _body = await _league_with_bracket(client, teams=2, rounds_for=4)
    res = await _confirm_bracket(client, headers, lid)
    assert res.status_code == 400, res.text
    league = await _get_league(client, headers, lid)
    assert league["bracketLocked"] is False
