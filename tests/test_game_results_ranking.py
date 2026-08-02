"""랭킹 정렬 검증 — 개인전(GET /api/game-results/stats의 sortOrder/tieGroup)과 팀전(GET /api/game-results/team-ranking).

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
        "/api/game-results",
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
    res = await client.get("/api/game-results/stats", headers=headers, params=params)
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
    # 팀전은 열 판을 채워야 점수가 나온다(_MIN_PLAYS_FOR_RANK) — 같은 대진을 열 번.
    for _ in range(10):
        await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "team1", TODAY)

    team = await _stats(client, headers, match_type="0102")
    # 이긴 편은 양수, 진 편은 음수. 같은 편끼리는 대칭이라 동점.
    assert team["player01"]["rankScore"] == team["player02"]["rankScore"] > 0
    assert team["player03"]["rankScore"] == team["player04"]["rankScore"] < 0
    assert team["player01"]["sortOrder"] < team["player03"]["sortOrder"]
    # 승패 기록은 경기 단위(2:2 한 판이면 1승/1패), 우열 인원은 상대별.
    assert team["player01"]["overall"]["plays"] == 10
    assert team["player01"]["overall"]["wins"] == 10
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


async def test_race_filter_scopes_rank_score(client):
    """종족 필터를 걸면 포인트(rankScore)도 그 종족 경기만으로 매겨진다.

    통계 화면의 승률/APM은 응답의 byRace에서 골라 쓰면 되지만, 포인트는 레이팅을 시간순으로
    누적해 만든 값이라 클라이언트가 종족별로 갈라낼 수 없다 — 종족 필터가 서버까지 가야
    한다(지적: 종족을 골라도 포인트만 전체 종족 기준으로 남는다).

    p1은 저그로는 세 번 다 이기고 테란으로는 세 번 다 진다. 전체 종족으로 보면 3승 3패로
    본전이지만, 저그만 보면 3승뿐이라 포인트가 더 높아야 한다. 종족별로 세 판씩인 건 종족
    필터를 걸면 최소 판수(개인전 3판)도 그 종족 판수로 세기 때문이다 — 두 판씩이면 점수가
    아예 안 나와서 비교 자체가 불가능하다(test_race_filter_min_plays_counts_that_race_only).
    """
    headers = await _signup_many(client, 3)

    async def game(p1_race: str, foe: str, result: str) -> None:
        res = await client.post(
            "/api/game-results",
            headers=headers,
            json={
                "date": TODAY, "result": result, "note": "", "matchType": "0101",
                "team1": [{"memberId": "player01", "race": p1_race}],
                "team2": [{"memberId": foe, "race": "테란"}],
            },
        )
        assert res.status_code == 200, res.text

    await game("저그", "player02", "team1")
    await game("저그", "player03", "team1")
    await game("저그", "player02", "team1")
    await game("테란", "player02", "team2")
    await game("테란", "player03", "team2")
    await game("테란", "player02", "team2")

    async def score(race: str | None) -> float:
        params = {"matchType": "0101"}
        if race:
            params["race"] = race
        res = await client.get("/api/game-results/stats", headers=headers, params=params)
        assert res.status_code == 200, res.text
        entry = {m["memberId"]: m for m in res.json()["members"]}["player01"]
        assert entry["rankScore"] is not None
        return entry["rankScore"]

    overall = await score(None)
    zerg = await score("저그")
    terran = await score("테란")

    assert zerg > overall, f"저그 전승인데 포인트가 전체({overall})보다 높지 않다: {zerg}"
    assert terran < overall, f"테란 전패인데 포인트가 전체({overall})보다 낮지 않다: {terran}"


async def test_rank_score_is_null_without_games(client):
    """이 기간·유형에 한 경기도 없는 회원은 점수를 내리지 않는다(요청: 경기 없는 0점은
    null로 내려서 화면에서 "-"로 보이게).

    0점은 '바닥까지 떨어진 점수'로 읽히는데, 실제로는 잰 적이 없다는 뜻이라 다른 말이다.
    붙은 사람과 안 붙은 사람이 같은 칸에 0으로 나란히 놓이면 순위표가 거짓이 된다."""
    headers = await _signup_many(client, 4)
    await _match(client, headers, ["player01"], ["player02"], "team1", TODAY)

    by_id = await _stats(client, headers)
    # 붙은 두 사람은 점수가 있고(승자 양수·패자 음수),
    assert by_id["player01"]["rankScore"] > 0
    assert by_id["player02"]["rankScore"] < 0
    # 한 판도 안 한 사람은 아예 값이 없다.
    assert by_id["player03"]["rankScore"] is None
    assert by_id["player04"]["rankScore"] is None
    # 그래도 순위표에는 남는다 — 0경기끼리 한 덩어리로 맨 아래에 놓인다.
    assert by_id["player03"]["sortOrder"] > by_id["player01"]["sortOrder"]


async def test_team_rank_ignores_min_plays(client):
    """포인트에는 최소 판수를 걸지 않는다(요청: "포인트 컬럼은 최소 경기수 제약을 적용
    안 하는 곳이야 — 편차가 없는 확정적 결과이기 때문").

    이 점수는 평균이 아니라 누적이라, 적게 뛴 사람은 못 믿을 값이 나오는 게 아니라 그냥
    적게 쌓인다. 한때 팀전 열 판을 채워야 점수가 나왔는데, 그 문턱을 걷어냈다."""
    headers = await _signup_many(client, 4)
    for _ in range(9):
        await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "team1", TODAY)

    nine = await _stats(client, headers, match_type="0102")
    assert nine["player01"]["overall"]["plays"] == 9
    assert nine["player01"]["rankScore"] > 0   # 아홉 판이어도 그대로 나온다
    assert nine["player03"]["rankScore"] < 0


async def test_solo_rank_ignores_min_plays(client):
    """개인전도 마찬가지 — 두 판만 뛰어도 포인트는 그대로 나온다. 한 판도 안 뛴 사람만
    값이 없다(0경기와 0점은 다른 말이라서)."""
    headers = await _signup_many(client, 3)
    for _ in range(2):
        await _match(client, headers, ["player01"], ["player02"], "team1", TODAY)

    two = await _stats(client, headers, match_type="0101")
    assert two["player01"]["overall"]["plays"] == 2
    assert two["player01"]["rankScore"] > 0
    assert two["player02"]["rankScore"] < 0
    assert two["player03"]["rankScore"] is None  # 안 뛴 사람만 없다


async def test_race_filter_counts_that_race_only(client):
    """종족 필터를 걸면 그 종족 판수로 센다 — 포인트는 판수와 무관하게 나오지만, 그 종족으로
    한 판도 안 뛰었으면(0경기) 여전히 값이 없다.

    p1은 개인전 4판(저그 2·테란 2)을 뛰었고 프로토스로는 한 판도 안 뛰었다."""
    headers = await _signup_many(client, 2)

    async def game(p1_race: str) -> None:
        res = await client.post(
            "/api/game-results",
            headers=headers,
            json={
                "date": TODAY, "result": "team1", "note": "", "matchType": "0101",
                "team1": [{"memberId": "player01", "race": p1_race}],
                "team2": [{"memberId": "player02", "race": "테란"}],
            },
        )
        assert res.status_code == 200, res.text

    for race in ("저그", "저그", "테란", "테란"):
        await game(race)

    async def entry(race: str | None) -> dict:
        params = {"matchType": "0101"}
        if race:
            params["race"] = race
        res = await client.get("/api/game-results/stats", headers=headers, params=params)
        assert res.status_code == 200, res.text
        return {m["memberId"]: m for m in res.json()["members"]}["player01"]

    # 포인트는 어느 쪽이든 나온다 — 두 판이어도 뛴 건 뛴 거다.
    assert (await entry(None))["rankScore"] is not None
    assert (await entry("저그"))["rankScore"] is not None
    assert (await entry("테란"))["rankScore"] is not None
    # 프로토스는 한 판도 안 뛰었다 → 포인트도 없다(0경기와 0점은 다른 말).
    assert (await entry("프로토스"))["rankScore"] is None


async def test_games_with_non_members_are_excluded_from_points(client, db_session):
    """컴퓨터·비회원이 낀 경기는 포인트 집계에서 통째로 빠진다(요청: "포인트 0점이 아니라
    게임으로 안 침").

    예전에는 그 자리만 레이팅 대상에서 빼고 경기 자체는 반영했다 — 그러면 남은 사람이
    '한 명 모자란 상대'와 싸운 것으로 계산돼 실제로 없던 실력차가 점수에 들어간다.

    전적(판수·승률)은 이 규칙과 무관하다 — 뛴 건 뛴 거다.

    회원 여부는 참가자 행이 아니라 replay_aliases(원본 게임 아이디 → 회원)로 판단하므로
    (models.py 주석), 그 매핑을 지우면 그 자리가 곧 비회원이 된다 — 등록 API는 회원만
    받으므로 여기서는 그렇게 만든다."""
    headers = await _signup_many(client, 5)
    # 2:2 팀전 — 그중 한 자리(player04)를 비회원으로 만든다. 1:1로는 이 규칙을 확인할 수
    # 없다: 상대 한 명이 통째로 빠지면 겨룰 상대가 없어 어느 쪽 규칙이든 점수가 안 움직인다.
    await _match(client, headers, ["player01", "player02"], ["player03", "player04"], "team1", TODAY)

    from sqlalchemy import delete

    from app.domain.members.models import ReplayAlias

    await db_session.execute(delete(ReplayAlias).where(ReplayAlias.raw_name == "player04"))
    await db_session.commit()

    by_id = await _stats(client, headers, match_type="0102")
    # 판수·승률은 그대로 센다.
    assert by_id["player01"]["overall"]["plays"] == 1
    assert by_id["player01"]["overall"]["wins"] == 1
    # 포인트는 안 움직인다 — 그 경기는 레이팅 계산에 들어가지 않는다.
    # (예전 규칙이었다면 남은 셋이 2:1로 붙은 것으로 계산돼 점수가 갈렸다.)
    assert by_id["player01"]["rankScore"] == 0
    assert by_id["player03"]["rankScore"] == 0

    # 회원끼리만 붙은 경기는 그대로 반영된다(같은 표에서 대조).
    await _match(client, headers, ["player01", "player02"], ["player03", "player05"], "team1", TODAY)
    by_id = await _stats(client, headers, match_type="0102")
    assert by_id["player01"]["rankScore"] > 0
    assert by_id["player03"]["rankScore"] < 0
