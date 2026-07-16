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


async def test_rank_order_puts_head_to_head_winner_first(client):
    """승자승이 1순위다 — 승률도, 승점도 보기 전에 "그 둘이 직접 붙었을 때 누가 이겼나"부터 본다.
    p1은 1전 1승(승점 +1), p2는 3전 2승 1패(승점 +1)로 승점이 같지만, 둘의 맞대결에서 p1이 이겼다."""
    headers = await _signup_many(client, 4)
    await _match(client, headers, ["player01"], ["player02"], "team1", TODAY)  # p1 > p2
    await _match(client, headers, ["player02"], ["player03"], "team1", TODAY)  # p2 > p3
    await _match(client, headers, ["player02"], ["player04"], "team1", TODAY)  # p2 > p4

    by_id = await _stats(client, headers)
    # 승률만 보면 p1(100%)이 p2(66.7%)보다 위지만, 그건 근거가 아니다 — 맞대결이 근거다.
    assert by_id["player01"]["sortOrder"] < by_id["player02"]["sortOrder"]
    assert by_id["player01"]["tieGroup"] != by_id["player02"]["tieGroup"]


async def test_rank_order_ties_when_no_basis_even_if_win_counts_differ(client):
    """맞대결도 공통상대도 없으면 전체 승수가 달라도 동급이다(요청: "간접비교 할게 없으면
    공동순위 무조건") — 예전의 ③전체 승수 기준을 없앴다. p1은 2승(p3 상대 2-0), p2는 1승
    (p4 상대 1-0)으로 승수가 다르지만, 둘은 서로도 공통상대도 없어 가를 근거가 없다."""
    headers = await _signup_many(client, 4)
    await _match(client, headers, ["player01"], ["player03"], "team1", TODAY)  # p1 > p3
    await _match(client, headers, ["player01"], ["player03"], "team1", TODAY)  # p1 > p3 (2승)
    await _match(client, headers, ["player02"], ["player04"], "team1", TODAY)  # p2 > p4 (1승)

    by_id = await _stats(client, headers)
    assert by_id["player01"]["overall"]["wins"] == 2
    assert by_id["player02"]["overall"]["wins"] == 1
    # 승수가 2 대 1로 달라도, 가를 근거(맞대결·공통상대)가 없으니 같은 tieGroup(공동순위)다.
    assert by_id["player01"]["tieGroup"] == by_id["player02"]["tieGroup"]


async def test_win_count_does_not_lift_over_unplayed_opponents(client):
    """요청 시나리오 — 한 명(p1=타센 역할)이 p2(팍규 역할)만 상대로 3승 1패이고 나머지와는
    전적이 없다. 예전엔 전체 승수(3승)로 p1이 안 붙어본 사람들 위(2위)로 올라갔지만, 이제
    승수 기준이 없어 p1은 '아무에게도 안 진' 최상위 공동순위이고 p2만 그 아래다."""
    headers = await _signup_many(client, 4)
    await _match(client, headers, ["player01"], ["player02"], "team1", TODAY)  # p1 > p2
    await _match(client, headers, ["player01"], ["player02"], "team1", TODAY)  # p1 > p2
    await _match(client, headers, ["player01"], ["player02"], "team1", TODAY)  # p1 > p2 (3승)
    await _match(client, headers, ["player02"], ["player01"], "team1", TODAY)  # p2 1승 (그래도 순 열세)
    await _match(client, headers, ["player03"], ["player04"], "team1", TODAY)  # p3 > p4 (타센/팍규와 무관)

    by_id = await _stats(client, headers)
    # p1은 p2를 승자승으로 이겨 위, p2는 아래.
    assert by_id["player01"]["tieGroup"] < by_id["player02"]["tieGroup"]
    # p1은 나머지(p3)와 가를 근거가 없다 → 둘 다 '안 진' 최상위 동급.
    assert by_id["player01"]["tieGroup"] == by_id["player03"]["tieGroup"]


async def test_rank_order_falls_back_to_common_opponents(client):
    """맞대결도 없고 승점도 같으면, 둘 다 붙어본 상대(공통상대)에 대한 승점으로 가른다.
    p1/p2는 각각 1승 1패(승점 0)이고 서로 만난 적이 없다 — 공통상대는 p3 하나뿐인데
    p1은 p3에게 이겼고(+1) p2는 졌다(-1)."""
    headers = await _signup_many(client, 6)
    await _match(client, headers, ["player01"], ["player03"], "team1", TODAY)  # p1 > p3
    await _match(client, headers, ["player04"], ["player01"], "team1", TODAY)  # p4 > p1
    await _match(client, headers, ["player03"], ["player02"], "team1", TODAY)  # p3 > p2
    await _match(client, headers, ["player02"], ["player05"], "team1", TODAY)  # p2 > p5

    by_id = await _stats(client, headers)
    assert by_id["player01"]["overall"]["wins"] == by_id["player02"]["overall"]["wins"] == 1
    assert by_id["player01"]["overall"]["losses"] == by_id["player02"]["overall"]["losses"] == 1

    assert by_id["player01"]["sortOrder"] < by_id["player02"]["sortOrder"]
    assert by_id["player01"]["tieGroup"] != by_id["player02"]["tieGroup"]


async def test_rank_order_marks_full_ties_as_same_tie_group(client):
    """모든 기준(맞대결 → 공통상대 → 승수)이 같으면 공동순위 — 셋이 물고 물리는
    순환(p1>p2>p3>p1)이라 승점도 전원 0, 공통상대도 없고 승수도 1로 같다.

    순환에서는 "이겼는데 아래"인 쌍이 반드시 하나 생긴다(원리적으로 순서를 정할 수 없다) —
    그래도 정렬은 끝나야 하고, 같은 입력이면 매번 같은 결과가 나와야 한다."""
    headers = await _signup_many(client, 3)
    await _match(client, headers, ["player01"], ["player02"], "team1", TODAY)
    await _match(client, headers, ["player02"], ["player03"], "team1", TODAY)
    await _match(client, headers, ["player03"], ["player01"], "team1", TODAY)

    by_id = await _stats(client, headers)
    orders = [by_id[f"player0{i}"]["sortOrder"] for i in (1, 2, 3)]
    assert sorted(orders) == [0, 1, 2]  # 순서 자체는 항상 결정된다

    # 두 번 조회해도 같은 결과 — 순환이어도 매 요청 흔들리지 않는다.
    again = await _stats(client, headers)
    assert [again[f"player0{i}"]["sortOrder"] for i in (1, 2, 3)] == orders


async def test_rank_order_is_none_for_members_without_matches(client):
    headers = await _signup_many(client, 2)
    by_id = await _stats(client, headers)
    assert by_id["player01"]["sortOrder"] is None
    assert by_id["player01"]["tieGroup"] is None


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
                    {"memberId": "__unregistered__x", "race": "저그"},
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
