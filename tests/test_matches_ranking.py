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


async def _stats(client, headers, match_type: str | None = None) -> dict:
    params = {"matchType": match_type} if match_type else None
    res = await client.get("/api/matches/stats", headers=headers, params=params)
    assert res.status_code == 200, res.text
    return {m["memberId"]: m for m in res.json()["members"]}


TODAY = date.today().isoformat()


async def test_rank_beating_strong_opponent_scores_more(client):
    """센 상대를 이길수록 점수가 크다 — 강함 = 1 + max(0, 순우열). p1은 순우열 -1인 p3(강함
    1)를 이겨 +1, p2는 순우열 +1인 p4(강함 2)를 이겨 +2라 p2가 위."""
    headers = await _signup_many(client, 6)
    await _match(client, headers, ["player01"], ["player03"], "team1", TODAY)  # p1 > p3
    await _match(client, headers, ["player02"], ["player04"], "team1", TODAY)  # p2 > p4(강함)
    await _match(client, headers, ["player04"], ["player05"], "team1", TODAY)  # p4가 강함
    await _match(client, headers, ["player04"], ["player06"], "team1", TODAY)

    by_id = await _stats(client, headers)
    assert by_id["player02"]["rankScore"] == 2   # p4 강함 2(순우열 +1)
    assert by_id["player01"]["rankScore"] == 1   # p3 강함 1(순우열 -1)
    assert by_id["player02"]["sortOrder"] < by_id["player01"]["sortOrder"]


async def test_rank_losing_to_weak_hurts_more(client):
    """약한 상대에게 지면 크게 깎이고 센 상대에게 지면 조금만 깎인다. p1은 여기저기 지는 약한
    p3(약함 3)에게 져서 -3, p2는 안 지는 센 p4(약함 1)에게 져서 -1이라 p2가 위."""
    headers = await _signup_many(client, 6)
    await _match(client, headers, ["player03"], ["player01"], "team1", TODAY)  # p3 > p1
    await _match(client, headers, ["player05"], ["player03"], "team1", TODAY)  # p3 약함(짐)
    await _match(client, headers, ["player06"], ["player03"], "team1", TODAY)
    await _match(client, headers, ["player04"], ["player02"], "team1", TODAY)  # p4 > p2(센)

    by_id = await _stats(client, headers)
    assert by_id["player01"]["rankScore"] == -2  # p3 약함 2(순우열 -1)
    assert by_id["player02"]["rankScore"] == -1  # p4 약함 1(순우열 +1, 순 승자라 최소 -1)
    assert by_id["player02"]["sortOrder"] < by_id["player01"]["sortOrder"]


async def test_rank_repeated_wins_accumulate_per_game(client):
    """경기마다 합산이라 같은 사람을 여러 번 이기면 그만큼 누적된다 — p1이 p2(약함 1)를 3번
    이겨 +3, p2는 p1(약함 1, 순 승자라 최소)에게 3번 져 -3."""
    headers = await _signup_many(client, 2)
    for _ in range(3):
        await _match(client, headers, ["player01"], ["player02"], "team1", TODAY)

    by_id = await _stats(client, headers)
    assert by_id["player01"]["rankScore"] == 3   # 3경기 × 강함1(p2 순우열 -1)
    assert by_id["player02"]["rankScore"] == -3  # 3경기 × -약함1(p1 순우열 +1)


async def test_rank_player_beats_no_show_even_when_negative(client):
    """1경기라도 뛰면 점수가 음수여도 0경기 회원보다 무조건 위(요청). 0경기 회원도 목록에
    나오고 맨 아래 공동."""
    headers = await _signup_many(client, 4)
    await _match(client, headers, ["player02"], ["player01"], "team1", TODAY)  # p1 짐(음수)

    by_id = await _stats(client, headers)
    assert by_id["player01"]["rankScore"] < 0             # p1 음수
    assert by_id["player03"]["sortOrder"] is not None     # 0경기도 순위가 매겨짐
    assert by_id["player01"]["tieGroup"] < by_id["player03"]["tieGroup"]  # 진 p1 > 안 뛴 p3
    assert by_id["player03"]["tieGroup"] == by_id["player04"]["tieGroup"]  # 안 뛴 둘 공동


async def test_rank_ties_ordered_by_nickname(client):
    """점수가 같으면 동률(같은 tieGroup) — 나열만 닉네임 순. p1·p2가 각각 대칭적인 약체
    한 명(강함 1)을 이겨 점수가 1로 같다."""
    headers = await _signup_many(client, 4)
    await _match(client, headers, ["player01"], ["player03"], "team1", TODAY)
    await _match(client, headers, ["player02"], ["player04"], "team1", TODAY)

    by_id = await _stats(client, headers)
    assert by_id["player01"]["rankScore"] == by_id["player02"]["rankScore"] == 1
    assert by_id["player01"]["tieGroup"] == by_id["player02"]["tieGroup"]


async def test_rank_draw_scores_zero(client):
    """비기면 0점(요청) — p1·p2가 한 번 비기면 둘 다 순우열 0, 점수 0으로 동률."""
    headers = await _signup_many(client, 2)
    await _match(client, headers, ["player01"], ["player02"], "draw", TODAY)

    by_id = await _stats(client, headers)
    assert by_id["player01"]["rankScore"] == 0
    assert by_id["player02"]["rankScore"] == 0
    assert by_id["player01"]["tieGroup"] == by_id["player02"]["tieGroup"]
    # 동률 안에서는 닉네임 순(Tag01 < Tag02) → p1이 앞.
    assert by_id["player01"]["sortOrder"] < by_id["player02"]["sortOrder"]


async def test_team_match_ranks_as_individual_cross_product(client):
    """팀전(0102) 개인 랭킹 — A팀[p1,p2]이 B팀[p3,p4]을 이기면 각 A가 각 B를 한 번씩 이긴
    것으로 풀리되(요청: "팀전도 개인 환산"), 팀전 점수엔 '팀 강함 비율' f=(진 팀 강함 합) ÷
    (이긴 팀 강함 합)을 곱한다. 이 한 경기로 A팀은 순우열 +2씩(강함 3), B팀은 -2씩(강함 1)이라
    이긴 팀 강함합 6, 진 팀 강함합 2 → f=2/6. p1·p2는 상대 강함합(2)×f≈0.7, p3·p4는 이긴
    팀 약함합(2)×f 만큼 잃어 -0.7(강한 팀으로 이기면 그만큼 적게 가져간다). matchType=0102로 조회했을 때만 잡힌다."""
    headers = await _signup_many(client, 4)
    await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "team1", TODAY)

    team = await _stats(client, headers, match_type="0102")
    assert team["player01"]["rankScore"] == 0.7    # 상대 강함합2 × f(2/6) ≈ 0.7
    assert team["player02"]["rankScore"] == 0.7
    assert team["player03"]["rankScore"] == -0.7   # 이긴팀 약함합2 × f(2/6) ≈ -0.7
    assert team["player04"]["rankScore"] == -0.7
    # 승패 기록은 경기 단위(2:2 한 판이면 1승/1패), 우열 인원은 상대별.
    assert team["player01"]["overall"]["plays"] == 1
    assert team["player01"]["overall"]["wins"] == 1
    assert team["player01"]["superiorCount"] == 2

    # 개인전(0101)으로 조회하면 이 팀경기는 안 잡혀 아무도 뛰지 않은 것으로 나온다.
    solo = await _stats(client, headers, match_type="0101")
    assert solo["player01"]["overall"]["plays"] == 0
    assert solo["player03"]["overall"]["plays"] == 0


async def test_strong_team_earns_less_for_same_strength_opponent(client):
    """팀 강함 비율의 핵심(요청) — 같은 세기의 상대를 이겨도 이긴 우리 팀이 셀수록 적게 얻는다.
    두 팀경기(둘 다 0102): M1 [p1,p2]>[p3,p4], M2 [p5,p6]>[p1,p2]. 전체 기간으로 보면 p5·p6은
    p1·p2를 이겨 강함6(순우열+2씩), p1·p2는 순우열0(강함2·약함2), p3·p4는 순우열-2(강함1·약함3).
    · M1: 이긴 p1·p2 강함합2 = 진 p3·p4 강함합2 → f=1, p1·p2는 상대 강함합2×1 = +2씩.
    · M2: 이긴 p5·p6 강함합6, 진 p1·p2 강함합2 → f=2/6, p5·p6은 상대 강함합2×(2/6) ≈ +0.7씩.
    상대(p1·p2 강함2, p3·p4 강함2)는 같은 세기인데 강한 p5·p6이 훨씬 적게 가져간다."""
    headers = await _signup_many(client, 6)
    await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "team1", TODAY)
    await _match(client, headers, ["player01", "player02"], ["player05", "player06"], "team2", TODAY)

    by_id = await _stats(client, headers)  # 필터 없이 전체
    assert by_id["player05"]["rankScore"] == 0.7   # 강한 팀이 이겨 적게
    assert by_id["player06"]["rankScore"] == 0.7
    assert by_id["player01"]["rankScore"] == 1.3   # M1 +2, M2 -0.7
    assert by_id["player03"]["rankScore"] == -2.0  # M1에서만, f=1
    # 강한 팀(p5·p6)이 같은 세기 상대를 이겼는데 약한 팀(p1·p2)이 M1에서 얻은 +2보다 적다.
    assert by_id["player05"]["rankScore"] < 2


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
