"""랭킹 정렬 검증 — 개인전(GET /api/matches/stats의 sortOrder/tieGroup)과 팀전(GET /api/matches/team-ranking).

정렬 규칙이 "동률일 때만 다음 단계로 넘어간다"는 단계형이라, 각 단계가 실제로 순서를 가르는
최소 픽스처를 단계별로 하나씩 만든다.
"""

from datetime import date


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
    """player01..playerNN을 만들고 첫 회원의 인증 헤더를 돌려준다."""
    first = None
    for i in range(1, count + 1):
        res = await _signup(client, f"player{i:02d}", f"Tag{i:02d}#100{i}")
        first = first or res
    return {"Authorization": f"Bearer {first['accessToken']}"}


async def _match(client, headers, team1: list[str], team2: list[str], result: str, when: str) -> None:
    def slots(ids: list[str]) -> list[dict]:
        return [{"memberId": i, "race": "테란"} for i in ids]

    res = await client.post(
        "/api/matches",
        headers=headers,
        json={
            "date": when, "team1": slots(team1), "team2": slots(team2),
            "result": result, "note": "",
            "matchType": "0102" if len(team1) > 1 or len(team2) > 1 else "0101",
        },
    )
    assert res.status_code == 200, res.text


async def _stats(client, headers) -> dict:
    res = await client.get("/api/matches/stats", headers=headers)
    assert res.status_code == 200, res.text
    return {m["memberId"]: m for m in res.json()["members"]}


TODAY = date.today().isoformat()


async def test_rank_head_to_head_no_longer_absolute(client):
    """승자승 절대우선은 폐기됐다(요청) — 더 많은 사람을 이긴 쪽이 위다. p1은 p2를 직접
    이겼지만 딱 그 한 명(기본점수 3), p2는 p3·p4를 이기고 p1에게만 져서(우세2·열세1 →
    3+3+1=7) 점수가 더 높아 p1보다 위로 온다."""
    headers = await _signup_many(client, 4)
    await _match(client, headers, ["player01"], ["player02"], "team1", TODAY)  # p1 > p2
    await _match(client, headers, ["player02"], ["player03"], "team1", TODAY)  # p2 > p3
    await _match(client, headers, ["player02"], ["player04"], "team1", TODAY)  # p2 > p4

    by_id = await _stats(client, headers)
    assert by_id["player02"]["sortOrder"] < by_id["player01"]["sortOrder"]
    assert by_id["player02"]["tieGroup"] < by_id["player01"]["tieGroup"]


async def test_rank_loss_beats_no_show_and_lists_all_members(client):
    """져도 참가점수(기본점수 1)를 받아 '아예 안 뛴 사람(0점)'보다 위다(요청). 그리고 0경기
    회원도 모두 목록에 나온다(sortOrder가 null이 아님) — 0점이라 맨 아래 공동."""
    headers = await _signup_many(client, 4)
    await _match(client, headers, ["player01"], ["player02"], "team1", TODAY)  # p1 이김, p2 짐

    by_id = await _stats(client, headers)
    # 0경기 p3·p4도 순위가 매겨진다(예전엔 null).
    assert by_id["player03"]["sortOrder"] is not None
    assert by_id["player04"]["sortOrder"] is not None
    # 진 p2(기본점수 1)가 아예 안 뛴 p3·p4(0점)보다 위.
    assert by_id["player02"]["tieGroup"] < by_id["player03"]["tieGroup"]
    # 안 뛴 둘은 맨 아래 공동.
    assert by_id["player03"]["tieGroup"] == by_id["player04"]["tieGroup"]


async def test_rank_more_participation_wins_even_all_losses(client):
    """참가 자체가 점수다 — 더 많은 사람과 붙으면(다 지더라도) 위로 간다. p1은 세 명에게 다
    졌지만(기본점수 3), p2는 한 명에게만 져서(1) p1이 위다."""
    headers = await _signup_many(client, 6)
    for opp in ("player03", "player04", "player05"):
        await _match(client, headers, [opp], ["player01"], "team1", TODAY)  # p1이 셋에게 다 짐
    await _match(client, headers, ["player06"], ["player02"], "team1", TODAY)  # p2가 한 명에게 짐

    by_id = await _stats(client, headers)
    assert by_id["player01"]["tieGroup"] < by_id["player02"]["tieGroup"]


async def test_rank_tie_broken_by_opponent_strength(client):
    """기본점수가 같으면 '누구와 붙었나'(SoS: 상대들의 기본점수 합)로 가른다 — 강한 상대와
    붙은 쪽이 위. p1·p2 둘 다 한 명을 이겨 기본점수 3으로 같지만, p1이 이긴 p3는 남들도
    이겨 강하고(점수 큼) p2가 이긴 p4는 약해서(작음) p1이 위다."""
    headers = await _signup_many(client, 6)
    await _match(client, headers, ["player01"], ["player03"], "team1", TODAY)  # p1 > p3
    await _match(client, headers, ["player02"], ["player04"], "team1", TODAY)  # p2 > p4
    await _match(client, headers, ["player03"], ["player05"], "team1", TODAY)  # p3도 강함
    await _match(client, headers, ["player03"], ["player06"], "team1", TODAY)

    by_id = await _stats(client, headers)
    # p1·p2 둘 다 한 명에게만 우세(기본점수 동일).
    assert by_id["player01"]["superiorCount"] == by_id["player02"]["superiorCount"] == 1
    # 강한 상대(p3)를 이긴 p1이 SoS로 위.
    assert by_id["player01"]["sortOrder"] < by_id["player02"]["sortOrder"]
    assert by_id["player01"]["tieGroup"] < by_id["player02"]["tieGroup"]


async def test_rank_full_tie_same_group_ordered_by_nickname(client):
    """기본점수도 SoS도 같으면 진짜 동률(같은 tieGroup) — 나열만 닉네임 순으로 가른다.
    p1·p2가 각각 대칭적인 약체 한 명을 이긴 경우."""
    headers = await _signup_many(client, 4)
    await _match(client, headers, ["player01"], ["player03"], "team1", TODAY)
    await _match(client, headers, ["player02"], ["player04"], "team1", TODAY)

    by_id = await _stats(client, headers)
    assert by_id["player01"]["tieGroup"] == by_id["player02"]["tieGroup"]
    assert by_id["player03"]["tieGroup"] == by_id["player04"]["tieGroup"]
    # 동률 안에서는 닉네임 순(Tag01 < Tag02) → p1이 앞.
    assert by_id["player01"]["sortOrder"] < by_id["player02"]["sortOrder"]


async def test_rank_isolated_islands_by_score(client):
    """서로 안 붙은 '섬'이 여럿이어도 기본점수(참가+우열)로 한 줄로 세우고, 동점은 SoS로 가른다.
      섬A: 미친(p1)이 태섭(p6)·곰세(p7)·크리스(p8)를 각각 3승 1패 → 우세3 (기본점수 9)
      섬B: 조조(p3) 1-1 군범(p4), 조조 1승 브래드(p5) → 조조 우세1·동등1(5) / 군범 동등1(2) / 브래드 열세1(1)
      섬C: 타센(p2) 3승 1패 팍규(p9) → 타센 우세1(3) / 팍규 열세1(1)
    기대: 미친(9) · 조조(5) · 타센(3) · 군범(2) · [태섭=곰세=크리스(1, SoS9)] · 브래드(1, SoS5) · 팍규(1, SoS3).
    핵심: 조조가 두 명 상대(우세1+동등1)라 타센(한 명)보다 위, 태섭들은 강한 미친에게 져서
    브래드·팍규(약체에게 짐)보다 위."""
    headers = await _signup_many(client, 9)

    for opp in ("player06", "player07", "player08"):
        for _ in range(3):
            await _match(client, headers, ["player01"], [opp], "team1", TODAY)
        await _match(client, headers, [opp], ["player01"], "team1", TODAY)
    await _match(client, headers, ["player03"], ["player04"], "team1", TODAY)
    await _match(client, headers, ["player04"], ["player03"], "team1", TODAY)
    await _match(client, headers, ["player03"], ["player05"], "team1", TODAY)
    for _ in range(3):
        await _match(client, headers, ["player02"], ["player09"], "team1", TODAY)
    await _match(client, headers, ["player09"], ["player02"], "team1", TODAY)

    by_id = await _stats(client, headers)
    # 우세/동등/열세 내역(카드용) + 우열점수.
    assert (by_id["player01"]["superiorCount"], by_id["player01"]["equalCount"], by_id["player01"]["inferiorCount"]) == (3, 0, 0)
    assert (by_id["player03"]["superiorCount"], by_id["player03"]["equalCount"], by_id["player03"]["inferiorCount"]) == (1, 1, 0)
    assert by_id["player01"]["personScore"] == 3

    g = {mid: m["tieGroup"] for mid, m in by_id.items()}
    # 기본점수 순: 미친 > 조조 > 타센 > 군범 (각자 다른 순위).
    assert g["player01"] < g["player03"] < g["player02"] < g["player04"]
    assert g["player04"] < g["player06"]
    # 기본점수 1로 같은 다섯 명 — SoS(누구에게 졌나)로 갈린다.
    assert g["player06"] == g["player07"] == g["player08"]   # 다 미친에게 짐(SoS 최고)
    assert g["player06"] < g["player05"] < g["player09"]     # 미친에게 짐 > 조조에게 짐 > 타센에게 짐


async def test_team_ranking_aggregates_actual_team_lineups(client):
    """실제로 같은 편이었던 2인 이상 구성만 팀으로 잡고, 승점(승 +1, 무 0, 패 -1) 순으로 줄세운다."""
    headers = await _signup_many(client, 4)
    # [p1,p2] vs [p3,p4] 2전: 2승 → +2점 / 2패 → -2점.
    await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "team1", TODAY)
    await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "team1", TODAY)
    # 편을 바꿔 한 번만 뛴 조합도 최소 경기수 기준이 없으니(요청: "팀랭킹 경기수 기준 삭제")
    # 그대로 랭킹에 오른다.
    await _match(client, headers, ["player01", "player03"], ["player02", "player04"], "draw", TODAY)
    # 1:1 경기는 팀(2인 이상)이 아니라 팀랭킹에 아예 안 잡힌다.
    await _match(client, headers, ["player01"], ["player02"], "team1", TODAY)

    res = await client.get("/api/matches/team-ranking", headers=headers)
    assert res.status_code == 200, res.text
    teams = res.json()["teams"]
    assert [t["memberIds"] for t in teams] == [
        ["player01", "player02"],
        ["player01", "player03"],
        ["player02", "player04"],
        ["player03", "player04"],
    ]
    assert teams[0] == {
        "memberIds": ["player01", "player02"], "plays": 2, "wins": 2, "losses": 0, "draws": 0, "points": 2,
    }
    assert teams[1] == {
        "memberIds": ["player01", "player03"], "plays": 1, "wins": 0, "losses": 0, "draws": 1, "points": 0,
    }
    assert teams[2] == {
        "memberIds": ["player02", "player04"], "plays": 1, "wins": 0, "losses": 0, "draws": 1, "points": 0,
    }
    assert teams[3] == {
        "memberIds": ["player03", "player04"], "plays": 2, "wins": 0, "losses": 2, "draws": 0, "points": -2,
    }


async def test_team_ranking_excludes_sides_with_a_placeholder_slot(client):
    """한 편에 컴퓨터/비회원이 한 명이라도 섞이면, 남은 실제 회원끼리를 더 작은(별개의)
    팀으로 잘못 집계해서는 안 된다 — 예: 3인 편(회원 2명+비회원 1명)이 회원 2명짜리 2인
    팀처럼 랭킹에 뜨는 버그가 실제로 있었다. 반대편([player03, player04])은 그 자체로
    깨끗한(비회원이 안 섞인) 2인 편이라 정상적으로 팀 랭킹에 잡혀야 한다."""
    headers = await _signup_many(client, 4)
    # [p1,p2,비회원] vs [p3,p4] 2전 — p1/p2 편에 비회원이 끼어 있으니 이 편은 팀으로
    # 잡히면 안 된다.
    for _ in range(2):
        res = await client.post(
            "/api/matches",
            headers=headers,
            json={
                "date": TODAY,
                "team1": [
                    {"memberId": "player01", "race": "테란"},
                    {"memberId": "player02", "race": "테란"},
                    {"memberId": "__unregistered__x", "race": "저그", "playerName": "GhostX"},
                ],
                "team2": [
                    {"memberId": "player03", "race": "테란"},
                    {"memberId": "player04", "race": "테란"},
                ],
                "result": "team1", "note": "", "matchType": "0102",
            },
        )
        assert res.status_code == 200, res.text

    res = await client.get("/api/matches/team-ranking", headers=headers)
    assert res.status_code == 200, res.text
    team_ids = [t["memberIds"] for t in res.json()["teams"]]
    assert ["player01", "player02"] not in team_ids
    assert ["player03", "player04"] in team_ids

    # 진짜 2:2(비회원 없이)로 두 번 뛰면 그제서야 [player01, player02]도 팀으로 잡힌다.
    await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "team1", TODAY)
    await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "team1", TODAY)
    res = await client.get("/api/matches/team-ranking", headers=headers)
    team_ids = [t["memberIds"] for t in res.json()["teams"]]
    assert ["player01", "player02"] in team_ids


async def test_team_ranking_shows_teams_after_a_single_match(client):
    """예전엔 2전 미만인 팀을 랭킹에서 숨겼지만(요청: "팀랭킹 경기수 기준 삭제") 이제
    최소 경기수 기준이 없어 단 한 번만 같이 뛰어도 바로 랭킹에 오른다."""
    headers = await _signup_many(client, 4)
    await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "team1", TODAY)

    res = await client.get("/api/matches/team-ranking", headers=headers)
    assert [t["memberIds"] for t in res.json()["teams"]] == [
        ["player01", "player02"], ["player03", "player04"],
    ]


async def test_team_ranking_counts_every_match_regardless_of_age(client):
    """dateFrom/dateTo를 안 넘기면 기간 조건이 없다 — 클럽 경기 수가 워낙 적어서 아무리
    오래된 경기도 그대로 집계에 들어간다(예전 동작 그대로)."""
    headers = await _signup_many(client, 4)
    for day in ("2020-01-01", "2020-01-02"):
        await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "team1", day)

    res = await client.get("/api/matches/team-ranking", headers=headers)
    teams = res.json()["teams"]
    assert [t["memberIds"] for t in teams] == [["player01", "player02"], ["player03", "player04"]]
    assert teams[0]["points"] == 2


async def test_team_ranking_date_range_narrows_to_that_period(client):
    """랭킹 화면의 월 기준 기본 집계용 — dateFrom/dateTo를 넘기면 그 기간 밖의 경기는
    plays 집계에서 아예 빠진다."""
    headers = await _signup_many(client, 4)
    await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "team1", "2026-01-05")
    await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "team1", "2026-01-06")
    await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "team1", "2026-02-05")

    res = await client.get(
        "/api/matches/team-ranking",
        headers=headers,
        params={"dateFrom": "2026-01-01", "dateTo": "2026-01-31"},
    )
    assert res.status_code == 200, res.text
    teams = res.json()["teams"]
    assert teams[0]["memberIds"] == ["player01", "player02"]
    assert teams[0]["plays"] == 2  # 2월 경기는 1월 범위 밖이라 빠진다.

    res_feb = await client.get(
        "/api/matches/team-ranking",
        headers=headers,
        params={"dateFrom": "2026-02-01", "dateTo": "2026-02-28"},
    )
    # 2월엔 1경기뿐이지만 최소 경기수 기준이 없으니 그대로 랭킹에 오른다.
    feb_teams = res_feb.json()["teams"]
    assert feb_teams[0]["memberIds"] == ["player01", "player02"]
    assert feb_teams[0]["plays"] == 1


async def test_stats_monthly_returns_one_entry_per_requested_month(client):
    """개인 랭킹의 월별 순위변동/전월 대비 화살표가 쓰는 배치 조회 — 달마다 그 달만의
    기간으로 다시 집계된 결과가 온다."""
    headers = await _signup_many(client, 2)
    await _match(client, headers, ["player01"], ["player02"], "team1", "2026-01-10")
    await _match(client, headers, ["player01"], ["player02"], "team1", "2026-02-10")
    await _match(client, headers, ["player01"], ["player02"], "team1", "2026-02-11")

    res = await client.get(
        "/api/matches/stats/monthly", headers=headers, params={"months": "2026-01,2026-02"},
    )
    assert res.status_code == 200, res.text
    months = res.json()["months"]
    assert [m["month"] for m in months] == ["2026-01", "2026-02"]
    jan_by_id = {m["memberId"]: m for m in months[0]["members"]}
    feb_by_id = {m["memberId"]: m for m in months[1]["members"]}
    assert jan_by_id["player01"]["overall"]["plays"] == 1
    assert feb_by_id["player01"]["overall"]["plays"] == 2


async def test_team_ranking_monthly_returns_one_entry_per_requested_month(client):
    headers = await _signup_many(client, 4)
    await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "team1", "2026-01-05")
    await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "team1", "2026-01-06")

    res = await client.get(
        "/api/matches/team-ranking/monthly", headers=headers, params={"months": "2026-01,2026-02"},
    )
    assert res.status_code == 200, res.text
    months = res.json()["months"]
    assert [m["month"] for m in months] == ["2026-01", "2026-02"]
    assert months[0]["teams"][0]["memberIds"] == ["player01", "player02"]
    assert months[1]["teams"] == []
