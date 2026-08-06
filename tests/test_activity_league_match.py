"""리그 경기 일정이 활동 목록에 뜨는가(요청: "리그 매치에 일정 등록시 활동에 띄움.
일정 수정되거나 결과 입력시 update 배지").

활동에 뜨는 조건은 '일정이 적혀 있는가' 하나다 — 대진표에 자리만 잡힌 경기는 아직 알릴
일이 없다. NEW/UPDATE를 가르는 두 시각(postedAt·updatedAt)도 여기서 함께 확인한다:
등록 직후에는 둘이 거의 같고(NEW만), 손대면 updatedAt만 뒤로 간다(UPDATE까지).
"""

from tests.test_league_match_result import _four_team_league, _set_result, _set_schedule
from tests.test_leagues import (
    _bootstrap,
    _create_league,
    _match,
    _save_bracket,
    _set_roster,
)


async def _feed(client, headers) -> list[dict]:
    res = await client.get("/api/activities", headers=headers, params={"limit": 50})
    assert res.status_code == 200, res.text
    return res.json()["items"]


def _league_items(items: list[dict]) -> list[dict]:
    return [i for i in items if i["kind"] == "leagueMatch"]


async def test_match_without_schedule_is_not_in_feed(client):
    """일정이 없는 경기는 활동에 안 뜬다 — 대진에 자리만 잡힌 것은 아직 알릴 일이 아니다."""
    headers, _ = await _bootstrap(client, 0)
    lid = (await _create_league(client, headers, best_of=1))["id"]
    await _save_bracket(client, headers, lid, ["", "a"])

    assert _league_items(await _feed(client, headers)) == []


async def test_schedule_registration_shows_in_feed(client):
    """일정을 적으면 그 경기가 활동에 뜬다 — 리그 이름·라운드·양 팀까지 함께."""
    headers, _ = await _bootstrap(client, 0)
    lid = (await _create_league(client, headers, best_of=1))["id"]
    body = (await _save_bracket(client, headers, lid, ["", "a"])).json()
    semi = _match(body, 1, 0)

    assert (await _set_schedule(client, headers, lid, semi["id"], "2026-09-01T20:30:00Z")).status_code == 200

    rows = _league_items(await _feed(client, headers))
    assert len(rows) == 1
    lm = rows[0]["leagueMatch"]
    assert lm["id"] == semi["id"]
    assert lm["leagueId"] == lid
    assert lm["scheduledAt"].startswith("2026-09-01T20:30:00")
    # 등록 직후에는 두 시각이 사실상 같다 — 화면은 이 차이로 NEW와 UPDATE를 가른다.
    assert lm["postedAt"] is not None and lm["updatedAt"] is not None


async def test_clearing_schedule_removes_it_from_feed(client):
    """일정을 지우면 활동에서도 내려간다 — 없어진 약속을 목록에 남겨 둘 이유가 없다."""
    headers, _ = await _bootstrap(client, 0)
    lid = (await _create_league(client, headers, best_of=1))["id"]
    body = (await _save_bracket(client, headers, lid, ["", "a"])).json()
    semi = _match(body, 1, 0)

    await _set_schedule(client, headers, lid, semi["id"], "2026-09-01T20:30:00Z")
    assert len(_league_items(await _feed(client, headers))) == 1

    await _set_schedule(client, headers, lid, semi["id"], None)
    assert _league_items(await _feed(client, headers)) == []


async def test_editing_schedule_keeps_posted_at_and_moves_updated_at(client):
    """일정을 고쳐도 '처음 적은 때'는 그대로다 — 그래야 NEW가 아니라 UPDATE로 읽힌다."""
    headers, _ = await _bootstrap(client, 0)
    lid = (await _create_league(client, headers, best_of=1))["id"]
    body = (await _save_bracket(client, headers, lid, ["", "a"])).json()
    semi = _match(body, 1, 0)

    await _set_schedule(client, headers, lid, semi["id"], "2026-09-01T20:30:00Z")
    first = _league_items(await _feed(client, headers))[0]["leagueMatch"]

    await _set_schedule(client, headers, lid, semi["id"], "2026-09-02T21:00:00Z")
    after = _league_items(await _feed(client, headers))[0]["leagueMatch"]

    assert after["postedAt"] == first["postedAt"]
    assert after["scheduledAt"].startswith("2026-09-02T21:00:00")
    assert after["updatedAt"] >= first["updatedAt"]


async def test_result_entry_shows_score_and_moves_updated_at(client):
    """결과를 넣으면 점수가 실리고 손댄 때가 뒤로 간다(요청: 결과 입력 시 UPDATE 배지).

    팀 이름은 로스터 닉네임으로 부른다 — 라벨(A·B)은 대진표 밖에서는 뜻이 없다.
    """
    headers, _ = await _bootstrap(client, 4)
    lid, teams, _ = await _four_team_league(client, headers)
    await _set_roster(client, headers, lid, teams[0]["id"], ["m0"])
    body = (await client.get(f"/api/leagues/{lid}", headers=headers)).json()
    semi = _match(body, 1, 0)

    await _set_schedule(client, headers, lid, semi["id"], "2026-09-01T20:30:00Z")
    before = _league_items(await _feed(client, headers))[0]["leagueMatch"]
    assert before["setsWonA"] is None

    assert (await _set_result(client, headers, lid, semi["id"], 2, 1)).status_code == 200

    after = next(
        i["leagueMatch"] for i in _league_items(await _feed(client, headers))
        if i["leagueMatch"]["id"] == semi["id"]
    )
    assert (after["setsWonA"], after["setsWonB"]) == (2, 1)
    assert after["winnerSide"] == "a"
    # 로스터는 사람 단위로 온다 — 카드가 세로로 한 줄씩 쌓기 때문이다(요청).
    assert [m["memberId"] for m in after["teamA"]["members"]] == ["m0"]
    assert after["teamB"]["members"] == []
    assert after["teamB"]["label"]
    assert after["postedAt"] == before["postedAt"]
    assert after["updatedAt"] >= before["updatedAt"]
