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
    """센 상대를 이길수록 레이팅이 더 오른다(요청: 랭킹점수=TrueSkill 레이팅). 레이팅은 경기를
    시간순으로 재생해 쌓이므로, p4를 먼저 두 번 이기게 해(p4>p5, p4>p6) 강자로 키운 뒤 — p1은
    신규(기본 레이팅) p3를, p2는 이미 강해진 p4를 이긴다. 둘 다 1승이지만 더 센 상대를 이긴 p2가
    위. 정확한 값은 β 튜닝에 따라 달라지므로 부호·대소 관계로만 검증한다."""
    headers = await _signup_many(client, 6)
    await _match(client, headers, ["player04"], ["player05"], "team1", TODAY)  # p4를 강자로
    await _match(client, headers, ["player04"], ["player06"], "team1", TODAY)
    await _match(client, headers, ["player01"], ["player03"], "team1", TODAY)  # p1 > 신규 p3
    await _match(client, headers, ["player02"], ["player04"], "team1", TODAY)  # p2 > 강한 p4

    by_id = await _stats(client, headers)
    assert by_id["player01"]["rankScore"] > 0
    # 더 센 상대를 이긴 p2가 신규를 이긴 p1보다 점수가 높다.
    assert by_id["player02"]["rankScore"] > by_id["player01"]["rankScore"]
    assert by_id["player02"]["sortOrder"] < by_id["player01"]["sortOrder"]


async def test_rank_losing_to_weak_hurts_more(client):
    """약한 상대에게 지면 더 깎이고 센 상대에게 지면 덜 깎인다. 레이팅은 시간순 재생이라 p3을
    먼저 두 번 지게 해(p5>p3, p6>p3) 약자로 만든 뒤 — 약한 p3이 p1을 이기고, 강한(기본) p4가
    p2를 이긴다. 둘 다 1패지만 더 약한 상대에게 진 p1이 더 낮다. 값은 β 튜닝에 흔들리므로
    부호·대소로만 검증."""
    headers = await _signup_many(client, 6)
    await _match(client, headers, ["player05"], ["player03"], "team1", TODAY)  # p3을 약자로
    await _match(client, headers, ["player06"], ["player03"], "team1", TODAY)
    await _match(client, headers, ["player03"], ["player01"], "team1", TODAY)  # 약한 p3 > p1
    await _match(client, headers, ["player04"], ["player02"], "team1", TODAY)  # 강한 p4 > p2

    by_id = await _stats(client, headers)
    # 더 약한 상대에게 진 p1이 더 센 상대에게 진 p2보다 낮다(둘 다 음수).
    assert by_id["player01"]["rankScore"] < by_id["player02"]["rankScore"] < 0
    assert by_id["player02"]["sortOrder"] < by_id["player01"]["sortOrder"]


async def test_rank_repeated_wins_accumulate_per_game(client):
    """레이팅은 경기마다 누적되므로 같은 상대를 여러 번 이기면 그만큼 더 쌓인다 — p1이 p2를 3번
    이기고(대조군으로 p3은 p4를 1번만 이긴다), 3승 누적한 p1이 1승뿐인 p3보다 높고, 3패한 p2가
    1패뿐인 p4보다 낮다. 승자는 양수·패자는 음수(요청: 승리 0 이상, 패배 0 이하)."""
    headers = await _signup_many(client, 4)
    for _ in range(3):
        await _match(client, headers, ["player01"], ["player02"], "team1", TODAY)
    await _match(client, headers, ["player03"], ["player04"], "team1", TODAY)  # 1승 대조군

    by_id = await _stats(client, headers)
    # 3승 누적 > 1승 > 0, 3패 누적 < 1패 < 0.
    assert by_id["player01"]["rankScore"] > by_id["player03"]["rankScore"] > 0
    assert by_id["player02"]["rankScore"] < by_id["player04"]["rankScore"] < 0


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
    # 완전히 대칭인 상황이라 레이팅(rankScore)이 같아 동률(같은 tieGroup)이고, 나열만 닉네임 순.
    assert by_id["player01"]["rankScore"] == by_id["player02"]["rankScore"] > 0
    assert by_id["player01"]["tieGroup"] == by_id["player02"]["tieGroup"]
    assert by_id["player01"]["sortOrder"] < by_id["player02"]["sortOrder"]


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
    것으로 풀린다(요청: "팀전도 개인 환산"). 레이팅(rankScore)은 이긴 편이 양수·진 편이 음수로
    갈리고, 대칭이라 같은 편끼리 동점. 승패 기록은 경기 단위(2:2 한 판=1승/1패), 우열 인원은
    상대별(각 2명). matchType=0102에서만 잡힌다."""
    headers = await _signup_many(client, 4)
    await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "team1", TODAY)

    team = await _stats(client, headers, match_type="0102")
    # 이긴 편은 양수, 진 편은 음수. 같은 편끼리는 대칭이라 동점.
    assert team["player01"]["rankScore"] == team["player02"]["rankScore"] > 0
    assert team["player03"]["rankScore"] == team["player04"]["rankScore"] < 0
    assert team["player01"]["sortOrder"] < team["player03"]["sortOrder"]
    # 승패 기록은 경기 단위(2:2 한 판이면 1승/1패), 우열 인원은 상대별.
    assert team["player01"]["overall"]["plays"] == 1
    assert team["player01"]["overall"]["wins"] == 1
    assert team["player01"]["superiorCount"] == 2

    # 개인전(0101)으로 조회하면 이 팀경기는 안 잡혀 아무도 뛰지 않은 것으로 나온다.
    solo = await _stats(client, headers, match_type="0101")
    assert solo["player01"]["overall"]["plays"] == 0
    assert solo["player03"]["overall"]["plays"] == 0


async def test_team_match_rating_is_time_ordered(client):
    """팀전도 레이팅은 시간순으로 누적된다 — 두 팀경기: M1 [p1,p2]>[p3,p4], M2 [p5,p6]>[p1,p2].
    p5·p6은 (이미 한 판 이겨 레이팅이 오른) p1·p2를 이겨 가장 높고, p1·p2는 1승1패라 소폭
    양수, p3·p4는 1패라 음수. 같은 편끼리는 대칭이라 동점. 값 자체는 β 튜닝에 흔들리므로
    대소·부호로만 검증한다."""
    headers = await _signup_many(client, 6)
    await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "team1", TODAY)
    await _match(client, headers, ["player01", "player02"], ["player05", "player06"], "team2", TODAY)

    by_id = await _stats(client, headers)  # 필터 없이 전체
    assert by_id["player05"]["rankScore"] == by_id["player06"]["rankScore"]
    assert by_id["player01"]["rankScore"] == by_id["player02"]["rankScore"]
    assert by_id["player03"]["rankScore"] == by_id["player04"]["rankScore"]
    # 강해진 상대를 이긴 p5 > 1승1패 p1 > 0 > 1패뿐인 p3.
    assert by_id["player05"]["rankScore"] > by_id["player01"]["rankScore"] > 0
    assert by_id["player01"]["rankScore"] > by_id["player03"]["rankScore"]
    assert by_id["player03"]["rankScore"] < 0


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
