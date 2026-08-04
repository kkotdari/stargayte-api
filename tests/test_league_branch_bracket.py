"""판을 우승 자리에서 역으로 키운다(요청).

    "시드 수를 정하고 시작하는 게 아니라 최종 승리자 한 칸에서 역으로 시작해서 대진을
     만드는 거야. 한 칸 왼쪽에 + 버튼을 누르면 거기서 두 개로 갈라진 가지가 생기고, 각
     칸에서 또 버튼을 눌러서 각각 가지를 치는 거지. 그러면 내가 필요한 데만 가지를 늘릴 수
     있어. 그다음부터는 지금처럼 아무 데나 시드를 배정할 수 있는 거야"
    "가지 친 상태에선 버튼이 −로 바뀌어서 가지를 삭제도 가능해야 하고"

이 규칙이 예전의 '판 크기를 미리 정하기'와 '안 쓰는 가지는 확정할 때 죽이기'를 대신한다 —
안 쓸 칸은 애초에 만들지 않는다. 부전승도 여전히 따로 찍지 않는다: 상대가 올라올 가지가
없는 자리가 곧 부전승이다.
"""

from tests.test_leagues import (
    _add_teams,
    _bootstrap,
    _branch,
    _confirm_bracket,
    _create_league,
    _generate_bracket,
    _get_league,
    _match,
    _seed,
    _start_bracket,
)


async def test_compound_bracket_built_by_branching(client):
    """요청한 복합 구조 — 네 팀 토너먼트 + 두 팀 단판, 그 둘이 결승.

    필요한 데만 가지를 치니 안 쓰는 칸이 아예 안 생긴다. 꽉 찬 8강이면 일곱 경기인데
    여기선 다섯이다."""
    headers, _ = await _bootstrap(client, 0)
    lid = (await _create_league(client, headers, best_of=1))["id"]
    teams = await _add_teams(client, headers, lid, 6)

    body = (await _start_bracket(client, headers, lid)).json()
    final = _match(body, 1, 0)
    # 결승 왼쪽은 네 팀 토너먼트로, 오른쪽은 두 팀 단판으로 키운다.
    body = (await _branch(client, headers, lid, final["id"], "a")).json()
    semi = _match(body, 1, 0)
    body = (await _branch(client, headers, lid, final["id"], "b")).json()
    single = _match(body, 1, 1)
    body = (await _branch(client, headers, lid, semi["id"], "a")).json()
    quarter_a = _match(body, 1, 0)
    body = (await _branch(client, headers, lid, semi["id"], "b")).json()
    quarter_b = _match(body, 1, 1)

    assert body["drawSize"] == 8
    assert len(body["matches"]) == 5      # 꽉 찬 8강이면 7경기
    assert body["plannedTeams"] == 6      # 8강 두 경기 + 단판 = 여섯 자리

    res = await _seed(client, headers, lid, [
        {"matchId": quarter_a["id"], "side": "a", "teamId": teams[0]["id"]},
        {"matchId": quarter_a["id"], "side": "b", "teamId": teams[1]["id"]},
        {"matchId": quarter_b["id"], "side": "a", "teamId": teams[2]["id"]},
        {"matchId": quarter_b["id"], "side": "b", "teamId": teams[3]["id"]},
        {"matchId": single["id"], "side": "a", "teamId": teams[4]["id"]},
        {"matchId": single["id"], "side": "b", "teamId": teams[5]["id"]},
    ])
    assert res.status_code == 200, res.text

    res = await _confirm_bracket(client, headers, lid)
    assert res.status_code == 200, res.text
    body = res.json()
    # 죽은 칸도, 부전승도 없다 — 여섯 팀이 전부 실제로 경기를 한다.
    assert all(not m["isDead"] for m in body["matches"])
    assert all(m["winnerTeamId"] is None for m in body["matches"])
    by_id = {m["id"]: m for m in body["matches"]}
    assert {by_id[single["id"]]["teamA"]["label"], by_id[single["id"]]["teamB"]["label"]} == {"E", "F"}
    # E·F는 2라운드에서 바로 붙는다 — 그 아래 1라운드 자리는 만들지 않았으니 없다.
    assert by_id[single["id"]]["round"] == 2
    assert [m for m in body["matches"] if m["round"] == 1] != []


async def test_seeding_a_branched_seat_is_rejected(client):
    """가지가 달린 자리엔 못 앉힌다(요청: 거부) — 거긴 아래 경기 승자가 올라올 자리다."""
    headers, _ = await _bootstrap(client, 0)
    lid = (await _create_league(client, headers, best_of=1))["id"]
    teams = await _add_teams(client, headers, lid, 4)
    body = (await _generate_bracket(client, headers, lid, 4)).json()
    final, left = _match(body, 2, 0), _match(body, 1, 0)

    res = await _seed(client, headers, lid, [
        {"matchId": final["id"], "side": "a", "teamId": teams[0]["id"]},
    ])
    assert res.status_code == 400, res.text
    assert "가지" in res.text

    # 가지가 없는 자리(결승 반대쪽은 아직 가지가 있으니, 1라운드 자리로) 는 된다.
    res = await _seed(client, headers, lid, [
        {"matchId": left["id"], "side": "a", "teamId": teams[0]["id"]},
    ])
    assert res.status_code == 200, res.text


async def test_a_seat_without_a_branch_becomes_a_bye(client):
    """상대가 올라올 가지가 없으면 그대로 올라간다 — 부전승은 따로 찍는 게 아니다."""
    headers, _ = await _bootstrap(client, 0)
    lid = (await _create_league(client, headers, best_of=1))["id"]
    teams = await _add_teams(client, headers, lid, 2)

    body = (await _start_bracket(client, headers, lid)).json()
    final = _match(body, 1, 0)
    body = (await _branch(client, headers, lid, final["id"], "a")).json()
    semi = _match(body, 1, 0)

    # A는 준결승에 혼자, B는 결승에 바로 앉는다 — 결승 b쪽엔 가지가 없다.
    res = await _seed(client, headers, lid, [
        {"matchId": semi["id"], "side": "a", "teamId": teams[0]["id"]},
        {"matchId": final["id"], "side": "b", "teamId": teams[1]["id"]},
    ])
    assert res.status_code == 200, res.text

    body = (await _confirm_bracket(client, headers, lid)).json()
    by_id = {m["id"]: m for m in body["matches"]}
    assert by_id[semi["id"]]["winnerTeamId"] == teams[0]["id"]      # 부전승으로 올라간다
    decider = by_id[final["id"]]
    assert decider["teamA"]["label"] == "A" and decider["teamB"]["label"] == "B"
    assert decider["winnerTeamId"] is None                          # 결승은 실제로 붙는다


async def test_confirm_kills_an_empty_branch_but_keeps_the_rows(client):
    """가지를 쳐 놓고 아무도 안 앉히면 확정할 때 죽는다 — 지우지 않고 표시만 한다(요청)."""
    headers, _ = await _bootstrap(client, 0)
    lid = (await _create_league(client, headers, best_of=1))["id"]
    teams = await _add_teams(client, headers, lid, 2)
    body = (await _generate_bracket(client, headers, lid, 4)).json()
    before = len(body["matches"])
    left = _match(body, 1, 0)

    res = await _seed(client, headers, lid, [
        {"matchId": left["id"], "side": "a", "teamId": teams[0]["id"]},
        {"matchId": left["id"], "side": "b", "teamId": teams[1]["id"]},
    ])
    assert res.status_code == 200, res.text

    body = (await _confirm_bracket(client, headers, lid)).json()
    assert len(body["matches"]) == before          # 한 줄도 안 지웠다
    assert _match(body, 1, 1)["isDead"] is True    # 아무도 안 앉은 가지
    assert _match(body, 1, 0)["isDead"] is False
    assert _match(body, 2, 0)["isDead"] is False   # 왼쪽 결과를 기다리는 진짜 결승


async def test_confirm_is_blocked_when_nobody_is_seeded(client):
    """아무도 안 앉은 판은 확정할 수 없다 — 굳힐 것이 없으면 판이 통째로 죽는다."""
    headers, _ = await _bootstrap(client, 0)
    lid = (await _create_league(client, headers, best_of=1))["id"]
    await _generate_bracket(client, headers, lid, 2)
    res = await _confirm_bracket(client, headers, lid)
    assert res.status_code == 400, res.text
    league = await _get_league(client, headers, lid)
    assert league["bracketLocked"] is False


async def test_bracket_shape_is_locked_after_confirm(client):
    """확정 뒤엔 모양도 못 고친다 — 가지 치기·지우기가 함께 막힌다."""
    headers, _ = await _bootstrap(client, 0)
    lid = (await _create_league(client, headers, best_of=1))["id"]
    teams = await _add_teams(client, headers, lid, 2)
    body = (await _generate_bracket(client, headers, lid, 2)).json()
    final = _match(body, 1, 0)

    res = await _seed(client, headers, lid, [
        {"matchId": final["id"], "side": "a", "teamId": teams[0]["id"]},
        {"matchId": final["id"], "side": "b", "teamId": teams[1]["id"]},
    ])
    assert res.status_code == 200, res.text
    assert (await _confirm_bracket(client, headers, lid)).status_code == 200

    assert (await _branch(client, headers, lid, final["id"], "a")).status_code == 409
    assert (await _start_bracket(client, headers, lid)).status_code == 409
