"""경기 일시와 결과(요청: "리그에 일시 추가하는거랑 결과입력도 필요한데 / 결과는 몇 대 몇 입력").

일시는 대진이 굳기 전에도 적을 수 있고, 결과는 확정 뒤에만 적을 수 있다 — 확정 전에는
시드가 아직 움직여서, 그때 적은 결과는 모양이 바뀌는 순간 뜻을 잃는다.
"""

from tests.test_leagues import (
    _add_teams,
    _bootstrap,
    _confirm_bracket,
    _create_league,
    _match,
    _save_bracket,
)


async def _set_schedule(client, headers, league_id: int, match_id: int, when: str | None):
    return await client.put(
        f"/api/leagues/{league_id}/matches/{match_id}/schedule",
        headers=headers, json={"scheduledAt": when},
    )


async def _set_result(client, headers, league_id: int, match_id: int, a: int | None, b: int | None):
    return await client.put(
        f"/api/leagues/{league_id}/matches/{match_id}/result",
        headers=headers, json={"setsWonA": a, "setsWonB": b},
    )


async def _four_team_league(client, headers, *, best_of: int = 3) -> tuple[int, list[dict], dict]:
    """A·B / C·D 4강 판을 만들어 확정까지 마친 리그를 돌려준다."""
    lid = (await _create_league(client, headers, best_of=best_of))["id"]
    teams = await _add_teams(client, headers, lid, 4)
    res = await _save_bracket(client, headers, lid, ["", "a", "b"], [
        {"path": "a", "side": "a", "teamId": teams[0]["id"]},
        {"path": "a", "side": "b", "teamId": teams[1]["id"]},
        {"path": "b", "side": "a", "teamId": teams[2]["id"]},
        {"path": "b", "side": "b", "teamId": teams[3]["id"]},
    ])
    assert res.status_code == 200, res.text
    res = await _confirm_bracket(client, headers, lid)
    assert res.status_code == 200, res.text
    return lid, teams, res.json()


async def test_schedule_is_writable_before_confirm_and_clearable(client):
    """일시는 확정 전에도 적을 수 있다 — 언제 붙을지는 대진이 굳기 전에도 정해진다."""
    headers, _ = await _bootstrap(client, 0)
    lid = (await _create_league(client, headers, best_of=1))["id"]
    body = (await _save_bracket(client, headers, lid, ["", "a"])).json()
    semi = _match(body, 1, 0)

    res = await _set_schedule(client, headers, lid, semi["id"], "2026-09-01T20:30:00Z")
    assert res.status_code == 200, res.text
    by_id = {m["id"]: m for m in res.json()["matches"]}
    assert by_id[semi["id"]]["scheduledAt"].startswith("2026-09-01T20:30:00")

    res = await _set_schedule(client, headers, lid, semi["id"], None)
    assert res.status_code == 200, res.text
    assert {m["id"]: m for m in res.json()["matches"]}[semi["id"]]["scheduledAt"] is None

    # 대진표에 없는 경기는 404.
    assert (await _set_schedule(client, headers, lid, 999999, None)).status_code == 404


async def test_schedule_is_rejected_on_a_dead_seat(client):
    """열리지 않을 경기에는 일시를 안 적는다."""
    headers, _ = await _bootstrap(client, 0)
    lid = (await _create_league(client, headers, best_of=1))["id"]
    teams = await _add_teams(client, headers, lid, 2)
    res = await _save_bracket(client, headers, lid, ["", "a", "b"], [
        {"path": "a", "side": "a", "teamId": teams[0]["id"]},
        {"path": "a", "side": "b", "teamId": teams[1]["id"]},
    ])
    assert res.status_code == 200, res.text
    body = (await _confirm_bracket(client, headers, lid)).json()
    dead = _match(body, 1, 1)   # 아무도 안 앉은 오른쪽 가지
    assert dead["isDead"] is True

    res = await _set_schedule(client, headers, lid, dead["id"], "2026-09-01T20:30:00Z")
    assert res.status_code == 400, res.text


async def test_result_needs_a_confirmed_bracket(client):
    """확정 전에는 결과를 못 넣는다 — 시드가 아직 움직인다."""
    headers, _ = await _bootstrap(client, 0)
    lid = (await _create_league(client, headers, best_of=3))["id"]
    teams = await _add_teams(client, headers, lid, 2)
    body = (await _save_bracket(client, headers, lid, [""], [
        {"path": "", "side": "a", "teamId": teams[0]["id"]},
        {"path": "", "side": "b", "teamId": teams[1]["id"]},
    ])).json()
    final = _match(body, 1, 0)

    res = await _set_result(client, headers, lid, final["id"], 2, 0)
    assert res.status_code == 400, res.text


async def test_result_records_the_score_and_carries_the_winner_up(client):
    """몇 대 몇을 적으면 이긴 쪽이 다음 라운드로 올라간다 — 승자를 따로 고르지 않는다."""
    headers, _ = await _bootstrap(client, 0)
    lid, teams, body = await _four_team_league(client, headers)
    left, right, final = _match(body, 1, 0), _match(body, 1, 1), _match(body, 2, 0)

    res = await _set_result(client, headers, lid, left["id"], 2, 1)
    assert res.status_code == 200, res.text
    by_id = {m["id"]: m for m in res.json()["matches"]}
    assert by_id[left["id"]]["setsWonA"] == 2 and by_id[left["id"]]["setsWonB"] == 1
    assert by_id[left["id"]]["winnerTeamId"] == teams[0]["id"]
    assert by_id[final["id"]]["teamA"]["label"] == "A"

    res = await _set_result(client, headers, lid, right["id"], 0, 2)
    assert res.status_code == 200, res.text
    body = res.json()
    by_id = {m["id"]: m for m in body["matches"]}
    assert by_id[final["id"]]["teamB"]["label"] == "D"
    assert body["status"] == "active"

    res = await _set_result(client, headers, lid, final["id"], 2, 0)
    assert res.status_code == 200, res.text
    body = res.json()
    assert {m["id"]: m for m in body["matches"]}[final["id"]]["winnerTeamId"] == teams[0]["id"]
    assert body["status"] == "completed"


async def test_result_rejects_a_draw_an_empty_seat_and_too_many_sets(client):
    """스코어가 승자를 말해야 한다 — 동점도, 몇 판제를 넘는 승수도 안 된다."""
    headers, _ = await _bootstrap(client, 0)
    lid, _teams, body = await _four_team_league(client, headers, best_of=3)
    left, final = _match(body, 1, 0), _match(body, 2, 0)

    assert (await _set_result(client, headers, lid, left["id"], 1, 1)).status_code == 400
    assert (await _set_result(client, headers, lid, left["id"], 4, 0)).status_code == 400
    # 결승은 아직 두 자리가 비어 있다 — 올라올 팀이 정해지기 전엔 못 적는다.
    assert (await _set_result(client, headers, lid, final["id"], 2, 0)).status_code == 400
    # 한쪽만 보낸 스코어는 스키마에서 거부한다(422).
    res = await client.put(
        f"/api/leagues/{lid}/matches/{left['id']}/result", headers=headers, json={"setsWonA": 2},
    )
    assert res.status_code == 422, res.text


async def test_result_can_be_rewritten_after_undoing_the_round_above(client):
    """고쳐 적으면 위로 태워 둔 진출을 걷어내고 다시 태운다 — 위에 결과가 남아 있으면 막는다."""
    headers, _ = await _bootstrap(client, 0)
    lid, teams, body = await _four_team_league(client, headers)
    left, right, final = _match(body, 1, 0), _match(body, 1, 1), _match(body, 2, 0)

    await _set_result(client, headers, lid, left["id"], 2, 1)
    await _set_result(client, headers, lid, right["id"], 2, 0)
    assert (await _set_result(client, headers, lid, final["id"], 2, 0)).status_code == 200

    # 결승 결과가 있는 동안은 4강을 못 고친다 — 고치면 결승이 통째로 틀린 값이 된다.
    res = await _set_result(client, headers, lid, left["id"], 1, 2)
    assert res.status_code == 400, res.text

    res = await _set_result(client, headers, lid, final["id"], None, None)
    assert res.status_code == 200, res.text
    body = res.json()
    assert {m["id"]: m for m in body["matches"]}[final["id"]]["winnerTeamId"] is None
    assert body["status"] == "active"

    # 이제 고쳐 적으면 결승에 올라온 팀이 바뀐다.
    res = await _set_result(client, headers, lid, left["id"], 1, 2)
    assert res.status_code == 200, res.text
    by_id = {m["id"]: m for m in res.json()["matches"]}
    assert by_id[left["id"]]["winnerTeamId"] == teams[1]["id"]
    assert by_id[final["id"]]["teamA"]["label"] == "B"

    # 지우면 올라가 있던 팀도 함께 내려온다.
    res = await _set_result(client, headers, lid, left["id"], None, None)
    assert res.status_code == 200, res.text
    by_id = {m["id"]: m for m in res.json()["matches"]}
    assert by_id[left["id"]]["setsWonA"] is None
    assert by_id[final["id"]]["teamA"] is None
