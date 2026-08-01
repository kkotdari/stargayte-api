"""일일 랭크 변동 알림의 순위표(RankingShiftService._compute_standings) 검증.

한동안 "경기는 했는데 최소 경기수 미만인 사람이 전부 1위로 잡히는" 버그가 있었다(지적).
원인은 순위 계산 쪽이었다 — 최소 판수를 못 채운 사람은 점수를 null로 내리고 tie_group을
전부 같은 값(맨 아래 한 덩어리)으로 묶었는데, 그 달에 아직 아무도 문턱을 못 넘었으면
'맨 아래 한 덩어리'가 곧 순위표 전체가 돼서 모두 1위가 됐다.

이제 포인트에는 최소 판수를 걸지 않으므로(game_results/service.py의 _apply_rank_order)
점수가 다르면 순위도 갈린다. 그 결과를 스냅샷 쪽에서 확인한다.
"""

from datetime import date

TODAY = date.today().isoformat()


async def _signup(client, member_id: str, battletag: str) -> dict:
    res = await client.post(
        "/api/auth/signup",
        json={
            "id": member_id, "password": "pass1234", "battletag": battletag,
            "replayAliases": [member_id], "insta": "",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


async def _signup_many(client, count: int) -> dict:
    """player01..playerNN을 만들고 전원 활성으로 올린 뒤 첫 회원(관리자) 헤더를 돌려준다.

    가입 직후는 대기 상태라 순위표(_compute_standings)가 활성 회원만 보고 걸러 낸다 —
    활성화를 빼먹으면 첫 회원만 남아 이 테스트가 뜻하는 상황이 아예 안 만들어진다."""
    first = None
    for i in range(1, count + 1):
        res = await _signup(client, f"player{i:02d}", f"Tag{i:02d}#100{i}")
        first = first or res
    headers = {"Authorization": f"Bearer {first['accessToken']}"}
    for i in range(2, count + 1):
        res = await client.patch(
            f"/api/members/player{i:02d}/status", headers=headers, json={"status": "active"},
        )
        assert res.status_code == 200, res.text
    return headers


async def _solo(client, headers, winner: str, loser: str) -> None:
    res = await client.post(
        "/api/game-results",
        headers=headers,
        json={
            "date": TODAY, "result": "team1", "note": "", "matchType": "0101",
            "team1": [{"memberId": winner, "race": "테란"}],
            "team2": [{"memberId": loser, "race": "테란"}],
        },
    )
    assert res.status_code == 200, res.text


async def _recompute(client, headers) -> None:
    res = await client.post("/api/feed/ranking-shifts/recompute", headers=headers)
    assert res.status_code == 200, res.text


async def _shifts(client, headers, match_type: str = "0101") -> dict[str, dict]:
    """가장 최근 변동 스냅샷의 변동 목록(회원별). 피드 목록은 변동이 실제로 있었던 것만
    내려주므로, 부르는 쪽에서 먼저 기준선(_recompute 1회)을 깔아 둬야 한다."""
    res = await client.get("/api/feed/ranking-shifts", headers=headers, params={"limit": 50})
    assert res.status_code == 200, res.text
    rows = [s for s in res.json() if s["matchType"] == match_type]
    assert rows, "변동 스냅샷이 남지 않았다"
    return {e["memberId"]: e for e in rows[0]["shifts"]}


async def test_short_of_min_plays_still_gets_real_ranks(client):
    """개인전 최소 판수(3판)를 아무도 못 채운 달에도 순위가 1위부터 제대로 갈린다.

    p1은 두 판 다 이겼고 p2는 두 판 다 졌다 — 예전에는 둘 다 '판수 미달' 한 덩어리라
    나란히 1위였다."""
    headers = await _signup_many(client, 2)
    await _recompute(client, headers)  # 기준선(아직 아무도 안 뛴 상태)
    await _solo(client, headers, "player01", "player02")
    await _solo(client, headers, "player01", "player02")
    await _recompute(client, headers)

    by_id = await _shifts(client, headers)
    assert by_id["player01"]["to"] == 1
    assert by_id["player02"]["to"] == 2
    # 점수도 실려 나간다 — 예전에는 rank_score가 null이라 전원 0점이었다.
    assert by_id["player01"]["toPoints"] > 0
    assert by_id["player02"]["toPoints"] < 0


async def test_only_players_with_games_are_ranked(client):
    """한 판도 안 뛴 회원은 순위표에 아예 안 들어간다 — 0경기와 0점은 다른 말이다."""
    headers = await _signup_many(client, 3)
    await _recompute(client, headers)
    await _solo(client, headers, "player01", "player02")
    await _recompute(client, headers)

    by_id = await _shifts(client, headers)
    assert set(by_id) == {"player01", "player02"}
    # 그 달에 처음 순위가 잡힌 사람은 from이 없다 — 화면이 "N위(신규)"로 적는 근거다.
    assert by_id["player01"]["from"] is None
    assert by_id["player02"]["from"] is None


async def test_same_score_shares_a_rank(client):
    """점수가 같으면 공동 순위 — 대칭적으로 이긴 둘은 나란히 1위, 진 둘은 나란히 3위."""
    headers = await _signup_many(client, 4)
    await _recompute(client, headers)
    await _solo(client, headers, "player01", "player03")
    await _solo(client, headers, "player02", "player04")
    await _recompute(client, headers)

    by_id = {k: e["to"] for k, e in (await _shifts(client, headers)).items()}
    assert by_id["player01"] == by_id["player02"] == 1
    assert by_id["player03"] == by_id["player04"] == 3
