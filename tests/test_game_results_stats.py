"""GET /api/game-results/stats, GET /api/game-results/main-race, POST /api/game-results/duplicate-check 검증.

수치는 전부 손으로 계산 가능한 소규모 픽스처로 정확히 맞춰서 단정한다.
"""


async def _signup(client, member_id: str, battletag: str) -> dict:
    res = await client.post(
        "/api/auth/signup",
        json={
            "id": member_id,
            "password": "pass1234",
            "battletag": battletag,
            "replayAliases": [member_id],
            "insta": "",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _slot(member_id: str, race: str, apm=None, eapm=None, cmd=None, ecmd=None, build=None) -> dict:
    return {
        "memberId": member_id, "race": race,
        "apm": apm, "eapm": eapm, "cmdCount": cmd, "effectiveCmdCount": ecmd, "buildCount": build,
    }


async def _create_match(
    client, headers, date: str, team1: list[dict], team2: list[dict], result: str,
    duration_seconds: int | None = None, match_type: str | None = None,
) -> dict:
    # matchType은 서버 기본값이 "0101"이라(프론트가 팀 크기로 계산해 보내는 값),
    # 팀전 테스트는 명시적으로 넘겨야 한다.
    res = await client.post(
        "/api/game-results",
        headers=headers,
        json={
            "date": date, "team1": team1, "team2": team2, "result": result, "note": "",
            "durationSeconds": duration_seconds,
            **({"matchType": match_type} if match_type else {}),
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


async def _seed_matches(client, headers) -> None:
    # match1: player01(테란) 승 / player02(저그) 패. 10분(600초)짜리 경기로 둬서
    # 유효커맨드가 "분당" 값으로 계산되는지(400/10=40, 200/10=20) 검증할 수 있게 한다.
    await _create_match(
        client, headers, "2026-07-01",
        team1=[_slot("player01", "테란", 100, 80, 500, 400, build=300)],
        team2=[_slot("player02", "저그", 60, 50, 300, 200, build=150)],
        result="team1", duration_seconds=600,
    )
    # match2: player02(저그) 승 / player01(프로토스) 패 -- 종족을 바꿔서 종족별 분리를 검증한다.
    # 이것도 10분(600초)짜리라 두 경기를 합쳐도 분당 계산이 깔끔하게 떨어진다.
    await _create_match(
        client, headers, "2026-07-02",
        team1=[_slot("player02", "저그", 80, 60, 350, 240, build=180)],
        team2=[_slot("player01", "프로토스", 120, 90, 550, 420, build=340)],
        result="team1", duration_seconds=600,
    )
    # match3: 무승부, 리플레이 파싱 값 없음(수동 등록) -- 평균 계산에서 제외돼야 한다.
    await _create_match(
        client, headers, "2026-07-03",
        team1=[_slot("player01", "테란")],
        team2=[_slot("player02", "저그")],
        result="draw",
    )


async def test_stats_aggregates_exact_numbers(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}
    await _seed_matches(client, headers)

    res = await client.get("/api/game-results/stats", headers=headers, params={"memberIds": "player01,player02"})
    assert res.status_code == 200, res.text
    by_id = {m["memberId"]: m for m in res.json()["members"]}

    # avgEcmd는 이제 "분당"이 아니라 경기당 평균이다(요청: "그냥 유효커맨드로") —
    # (400+420)/2경기=410, 테란만이면 400, 프로토스만이면 420.
    p1_overall = by_id["player01"]["overall"]
    assert p1_overall == {
        "plays": 3, "wins": 1, "losses": 1, "draws": 1, "winRate": 33.3,
        "avgApm": 110, "avgEapm": 85, "avgCmd": 525, "avgEcmd": 410, "avgBuild": 320,
    }
    assert by_id["player01"]["byRace"]["테란"] == {
        "plays": 2, "wins": 1, "losses": 0, "draws": 1, "winRate": 50.0,
        "avgApm": 100, "avgEapm": 80, "avgCmd": 500, "avgEcmd": 400, "avgBuild": 300,
    }
    assert by_id["player01"]["byRace"]["프로토스"] == {
        "plays": 1, "wins": 0, "losses": 1, "draws": 0, "winRate": 0.0,
        "avgApm": 120, "avgEapm": 90, "avgCmd": 550, "avgEcmd": 420, "avgBuild": 340,
    }
    assert by_id["player01"]["byRace"]["저그"]["plays"] == 0
    assert by_id["player01"]["mostPlayedRace"] == "테란"  # 2판 > 1판

    # player02: (200+240)/2경기=220
    p2_overall = by_id["player02"]["overall"]
    assert p2_overall == {
        "plays": 3, "wins": 1, "losses": 1, "draws": 1, "winRate": 33.3,
        "avgApm": 70, "avgEapm": 55, "avgCmd": 325, "avgEcmd": 220, "avgBuild": 165,
    }
    assert by_id["player02"]["mostPlayedRace"] == "저그"


async def test_stats_excludes_extreme_outlier_game_from_eapm_ecmd_average(client):
    """리플레이 파싱 오류 등으로 유효APM/유효커맨드가 그 회원의 다른 경기들과 확 튀는
    경기 하나는 그 두 항목의 평균에서만 빠져야 한다(전적/APM/커맨드 등 나머지는 그대로).
    표본이 5개 이상이어야 이상치 판단을 하므로(service.py의 _OUTLIER_MIN_SAMPLES), 정상
    범위 경기 5개 + 이상치 경기 1개로 총 6경기를 구성한다."""
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    normal_eapm = [80, 82, 78, 81, 79]
    normal_ecmd = [400, 410, 390, 405, 395]  # 10분(600초)짜리라 분당 40 안팎
    for i, (eapm, ecmd) in enumerate(zip(normal_eapm, normal_ecmd)):
        await _create_match(
            client, headers, f"2026-07-{i + 1:02d}",
            team1=[_slot("player01", "테란", 100, eapm, 500, ecmd)],
            team2=[_slot("player02", "저그", 60, 50, 300, 200)],
            result="team1", duration_seconds=600,
        )
    # 6번째 경기만 유효APM(500)/유효커맨드(분당 600)가 나머지와 편차가 극심하게 튄다.
    await _create_match(
        client, headers, "2026-07-06",
        team1=[_slot("player01", "테란", 100, 500, 500, 6000)],
        team2=[_slot("player02", "저그", 60, 50, 300, 200)],
        result="team1", duration_seconds=600,
    )

    res = await client.get("/api/game-results/stats", headers=headers, params={"memberIds": "player01"})
    overall = res.json()["members"][0]["overall"]
    assert overall["plays"] == 6  # 전적 자체는 이상치 경기도 포함해서 그대로 6전
    # 이상치를 뺀 나머지 5경기만으로 평균 -> eapm 80, ecmd (400+410+390+405+395)/5경기=400
    assert overall["avgEapm"] == 80
    assert overall["avgEcmd"] == 400
    by_race = res.json()["members"][0]["byRace"]["테란"]
    assert by_race["avgEapm"] == 80
    assert by_race["avgEcmd"] == 400


async def test_stats_excludes_extreme_outlier_game_from_apm_average(client):
    """APM 평균도 튀는 경기 하나를 뺀다(지적: APM이 700대로 나온다).

    APM은 '분당' 값이라 한 판만 튀어도 평균이 통째로 끌려간다 — 열 판 넘게 정상으로 친
    사람도 700대가 된다. 유효APM은 처음부터 이 처리를 받고 있었는데 APM만 빠져 있었다.

    여기서 튀는 판은 길이가 정상(10분)이다 — 짧은 판은 경기 길이 기준(_MIN_DURATION_SECONDS)
    에 먼저 걸려서, 그걸 썼다간 중앙값/MAD 판정이 실제로 도는지를 검증하지 못한다."""
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    for i, apm in enumerate([150, 148, 152, 149, 151]):
        await _create_match(
            client, headers, f"2026-07-{i + 1:02d}",
            team1=[_slot("player01", "테란", apm, 120, 1500, 1200)],
            team2=[_slot("player02", "저그", 60, 50, 300, 200)],
            result="team1", duration_seconds=600,
        )
    # 6번째만 APM이 극단적으로 튄다(파싱 오류) — 길이는 나머지와 같은 10분이라 길이 기준에는
    # 안 걸리고 오직 중앙값/MAD 판정으로만 걸러져야 한다.
    await _create_match(
        client, headers, "2026-07-06",
        team1=[_slot("player01", "테란", 7000, 120, 1500, 1200)],
        team2=[_slot("player02", "저그", 60, 50, 300, 200)],
        result="team1", duration_seconds=600,
    )

    res = await client.get("/api/game-results/stats", headers=headers, params={"memberIds": "player01"})
    overall = res.json()["members"][0]["overall"]
    assert overall["plays"] == 6  # 전적 자체는 그대로 6전
    # 튄 판을 뺀 다섯 판의 평균 — 단순 평균이었다면 (750+7000)/6 = 1292가 됐다.
    assert overall["avgApm"] == 150
    assert res.json()["members"][0]["byRace"]["테란"]["avgApm"] == 150


async def test_stats_excludes_too_short_game_even_below_outlier_sample_floor(client):
    """2분 미만 경기는 표본이 몇 판이든 지표 평균에서 뺀다(_MIN_DURATION_SECONDS).

    중앙값/MAD 판정은 표본이 5판(_OUTLIER_MIN_SAMPLES) 미만이면 아예 안 돈다. 순위 최소
    판수가 개인전 3판으로 내려오면서 서너 판만 뛴 회원이 순위표에 오르는데, 그 구간은
    정확히 MAD의 사각지대였다 — 20초짜리 기록 하나가 그대로 평균에 들어갔다. 경기 길이
    기준은 표본이 한 판이어도 판단할 수 있어 그 구멍을 메운다.

    여기서는 정상 경기를 일부러 두 판만 둔다 — MAD가 돌았다면 검증이 성립하지 않는다."""
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    for i, apm in enumerate([150, 150]):
        await _create_match(
            client, headers, f"2026-07-{i + 1:02d}",
            team1=[_slot("player01", "테란", apm, 120, 1500, 1200, build=900)],
            team2=[_slot("player02", "저그", 60, 50, 300, 200, build=150)],
            result="team1", duration_seconds=600,
        )
    # 20초 만에 끝난 기록 — 나간 판이라 APM은 분당으로 치솟고, 커맨드·생산은 반대로 바닥이다.
    await _create_match(
        client, headers, "2026-07-03",
        team1=[_slot("player01", "테란", 7000, 4000, 230, 200, build=30)],
        team2=[_slot("player02", "저그", 60, 50, 300, 200, build=150)],
        result="team1", duration_seconds=20,
    )

    res = await client.get("/api/game-results/stats", headers=headers, params={"memberIds": "player01"})
    overall = res.json()["members"][0]["overall"]
    assert overall["plays"] == 3  # 전적은 그대로 3전 — 나간 판도 뛴 건 뛴 거다
    # 다섯 지표 모두 정상 두 판만으로 계산된다(단순 평균이었다면 APM 2433, 생산 610).
    assert overall["avgApm"] == 150
    assert overall["avgEapm"] == 120
    assert overall["avgCmd"] == 1500
    assert overall["avgEcmd"] == 1200
    assert overall["avgBuild"] == 900


async def test_stats_keeps_game_at_the_duration_floor(client):
    """경계값 자체(정확히 120초)는 남긴다 — 기준은 '미만'이라 2분짜리는 치른 판이다."""
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    await _create_match(
        client, headers, "2026-07-01",
        team1=[_slot("player01", "테란", 100, 80, 500, 400, build=300)],
        team2=[_slot("player02", "저그", 60, 50, 300, 200, build=150)],
        result="team1", duration_seconds=120,
    )
    # 1초만 모자란 판은 빠진다.
    await _create_match(
        client, headers, "2026-07-02",
        team1=[_slot("player01", "테란", 900, 800, 50, 40, build=10)],
        team2=[_slot("player02", "저그", 60, 50, 300, 200, build=150)],
        result="team1", duration_seconds=119,
    )

    res = await client.get("/api/game-results/stats", headers=headers, params={"memberIds": "player01"})
    overall = res.json()["members"][0]["overall"]
    assert overall["plays"] == 2
    assert overall["avgApm"] == 100  # 120초짜리 한 판만 반영 — 둘 다면 500이었다
    assert overall["avgBuild"] == 300


_METRIC_FIELDS = ("avgApm", "avgEapm", "avgCmd", "avgEcmd", "avgBuild")


async def _solo_matches(client, headers, n: int, *, race: str = "테란", start: int = 1) -> None:
    for i in range(n):
        await _create_match(
            client, headers, f"2026-07-{start + i:02d}",
            team1=[_slot("player01", race, 100, 80, 500, 400, build=300)],
            team2=[_slot("player02", "저그", 60, 50, 300, 200, build=150)],
            result="team1", duration_seconds=600, match_type="0101",
        )


async def _solo_stats(client, headers, **params) -> dict:
    res = await client.get(
        "/api/game-results/stats", headers=headers,
        params={"memberIds": "player01", "matchType": "0101", **params},
    )
    assert res.status_code == 200, res.text
    return res.json()["members"][0]


async def test_stats_hides_metric_averages_below_min_plays_but_keeps_record(client):
    """개인전 3판(_MIN_PLAYS)을 못 채우면 지표 평균은 전부 null이고, 전적·승률은 그대로다.

    판수가 적으면 이상치 판정(_OUTLIER_MIN_SAMPLES=5판)조차 못 돌아서 두 판 중 한 판만
    튀어도 평균이 그대로 끌려간다 — 잴 만큼 안 뛴 걸 숫자로 내보내는 게 더 나쁘다.
    반면 승패는 한 판을 뛰었으면 그 한 판의 결과가 있는 그대로의 사실이라 가리지 않는다
    (요청: "승률은 정확하니까 굳이 안 빼도 될듯")."""
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    await _solo_matches(client, headers, 2)
    overall = (await _solo_stats(client, headers))["overall"]
    assert [overall[f] for f in _METRIC_FIELDS] == [None] * 5
    # 전적은 그대로 — 2전 2승이면 승률 100%다.
    assert overall["plays"] == 2 and overall["wins"] == 2 and overall["winRate"] == 100.0

    # 세 판째에 지표가 비로소 나온다.
    await _solo_matches(client, headers, 1, start=3)
    overall = (await _solo_stats(client, headers))["overall"]
    assert overall["plays"] == 3
    assert [overall[f] for f in _METRIC_FIELDS] == [100, 80, 500, 400, 300]


async def test_stats_metric_gate_counts_only_the_filtered_race(client):
    """종족 필터를 걸면 그 종족 판수로 센다 — 기준값은 유형 그대로(개인전 3판).

    전체로는 4판이라 문턱을 넘지만, 저그로는 두 판뿐이라 저그만 보면 지표가 사라져야 한다."""
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    await _solo_matches(client, headers, 2, race="테란", start=1)
    await _solo_matches(client, headers, 2, race="저그", start=3)

    overall = (await _solo_stats(client, headers))["overall"]
    assert overall["plays"] == 4
    assert overall["avgApm"] == 100  # 전 종족 4판 → 통과

    zerg = (await _solo_stats(client, headers, race="저그"))["overall"]
    assert zerg["plays"] == 2
    assert [zerg[f] for f in _METRIC_FIELDS] == [None] * 5  # 저그 2판 → 미달
    assert zerg["winRate"] == 100.0  # 승률은 그대로


async def test_stats_metric_gate_is_per_block_so_byrace_matches_race_filter(client):
    """칸마다 자기 판수로 판단한다 — 종족을 걸어 본 숫자와 안 걸고 byRace에서 꺼낸 같은
    숫자가 서로 어긋나면 안 된다.

    테란 3판/저그 2판이면 전체(5판)와 테란 칸은 지표가 나오고 저그 칸만 null이다."""
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    await _solo_matches(client, headers, 3, race="테란", start=1)
    await _solo_matches(client, headers, 2, race="저그", start=4)

    member = await _solo_stats(client, headers)
    assert member["overall"]["plays"] == 5
    assert member["overall"]["avgApm"] == 100
    assert member["byRace"]["테란"]["avgApm"] == 100          # 3판 → 통과
    assert member["byRace"]["저그"]["avgApm"] is None          # 2판 → 미달
    assert member["byRace"]["저그"]["plays"] == 2              # 전적은 그대로

    # 종족을 걸어서 본 저그 칸도 같은 결론이어야 한다.
    zerg_filtered = (await _solo_stats(client, headers, race="저그"))["overall"]
    assert zerg_filtered["avgApm"] is member["byRace"]["저그"]["avgApm"]
    assert zerg_filtered["plays"] == member["byRace"]["저그"]["plays"]


async def test_stats_metric_gate_uses_ten_plays_for_team_matches(client):
    """팀전은 문턱이 10판이다 — 한 판의 결과를 넷이 나눠 갖는 자리라 표본이 더 얕다."""
    p1 = await _signup(client, "player01", "Shadow#1001")
    for i in range(2, 5):
        await _signup(client, f"player0{i}", f"P{i}#100{i}")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    async def team_matches(n: int, start: int) -> None:
        for i in range(n):
            await _create_match(
                client, headers, f"2026-07-{start + i:02d}",
                team1=[_slot("player01", "테란", 100, 80, 500, 400, build=300), _slot("player02", "저그")],
                team2=[_slot("player03", "프로토스"), _slot("player04", "테란")],
                result="team1", duration_seconds=600, match_type="0102",
            )

    async def team_stats() -> dict:
        res = await client.get(
            "/api/game-results/stats", headers=headers,
            params={"memberIds": "player01", "matchType": "0102"},
        )
        assert res.status_code == 200, res.text
        return res.json()["members"][0]["overall"]

    await team_matches(9, 1)
    nine = await team_stats()
    assert nine["plays"] == 9
    assert [nine[f] for f in _METRIC_FIELDS] == [None] * 5  # 아홉 판까지는 아직 없음
    assert nine["winRate"] == 100.0

    await team_matches(1, 10)
    ten = await team_stats()
    assert ten["plays"] == 10
    assert ten["avgApm"] == 100  # 열 판째에 비로소 나온다


async def test_stats_excludes_extreme_outlier_game_from_cmd_and_build_average(client):
    """커맨드(avgCmd)·생산(avgBuild) 평균에서도 튀는 경기를 뺀다.

    다섯 지표(APM/유효APM/커맨드/유효커맨드/생산)는 같은 리플레이 파싱에서 같이 나오는
    값이라 한 경기가 튀면 대개 같이 튄다. 한때 커맨드·생산만 SQL 단순 평균이 그대로 나가서,
    같은 경기가 유효커맨드 평균에서는 빠지고 총커맨드 평균에는 남아 화면에 나란히 놓인
    숫자끼리 앞뒤가 안 맞았다(유효커맨드보다 총커맨드가 비정상적으로 부풀어 보였다)."""
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    normal_cmd = [500, 510, 490, 505, 495]   # 평균 500
    normal_build = [300, 310, 290, 305, 295]  # 평균 300
    for i, (cmd, build) in enumerate(zip(normal_cmd, normal_build)):
        await _create_match(
            client, headers, f"2026-07-{i + 1:02d}",
            team1=[_slot("player01", "테란", 100, 80, cmd, 400, build=build)],
            team2=[_slot("player02", "저그", 60, 50, 300, 200, build=150)],
            result="team1", duration_seconds=600,
        )
    # 6번째 경기만 커맨드·생산이 나머지와 편차가 극심하게 튄다(파싱 오류로 자릿수가 어긋난 값).
    await _create_match(
        client, headers, "2026-07-06",
        team1=[_slot("player01", "테란", 100, 80, 9000, 400, build=7000)],
        team2=[_slot("player02", "저그", 60, 50, 300, 200, build=150)],
        result="team1", duration_seconds=600,
    )

    res = await client.get("/api/game-results/stats", headers=headers, params={"memberIds": "player01"})
    overall = res.json()["members"][0]["overall"]
    assert overall["plays"] == 6  # 전적 자체는 이상치 경기도 포함해서 그대로 6전
    # 튄 판을 뺀 다섯 판의 평균 — 단순 평균이었다면 커맨드 1917, 생산 1450이 됐다.
    assert overall["avgCmd"] == 500
    assert overall["avgBuild"] == 300
    by_race = res.json()["members"][0]["byRace"]["테란"]
    assert by_race["avgCmd"] == 500
    assert by_race["avgBuild"] == 300


async def test_stats_race_filter_scopes_overall(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}
    await _seed_matches(client, headers)

    res = await client.get(
        "/api/game-results/stats", headers=headers, params={"memberIds": "player01", "race": "프로토스"}
    )
    overall = res.json()["members"][0]["overall"]
    assert overall == {
        "plays": 1, "wins": 0, "losses": 1, "draws": 0, "winRate": 0.0,
        "avgApm": 120, "avgEapm": 90, "avgCmd": 550, "avgEcmd": 420, "avgBuild": 340,
    }
    # byRace/mostPlayedRace는 race 파라미터와 무관하게 항상 전체 종족 기준이어야 한다.
    assert res.json()["members"][0]["mostPlayedRace"] == "테란"


async def test_stats_member_with_zero_matches_returns_zero_defaults(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    res = await client.get("/api/game-results/stats", headers=headers, params={"memberIds": "player01"})
    entry = res.json()["members"][0]
    assert entry["overall"] == {
        "plays": 0, "wins": 0, "losses": 0, "draws": 0, "winRate": 0.0,
        "avgApm": None, "avgEapm": None, "avgCmd": None, "avgEcmd": None, "avgBuild": None,
    }
    assert entry["mostPlayedRace"] is None


async def test_duplicate_check_matches_regardless_of_timestamp_format(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    await client.post(
        "/api/game-results",
        headers=headers,
        json={
            "date": "2026-07-01",
            "team1": [{"memberId": "player01", "race": "테란"}],
            "team2": [{"memberId": "player02", "race": "저그"}],
            "result": "team1",
            "note": "",
            "gameStartedAt": "2026-07-01T10:00:00+00:00",
        },
    )

    res = await client.post(
        "/api/game-results/duplicate-check",
        headers=headers,
        json={"gameStartedAt": ["2026-07-01T10:00:00Z", "2026-07-02T10:00:00Z"]},
    )
    assert res.status_code == 200, res.text
    # "Z"로 보냈지만 실제 저장은 "+00:00"으로 돼 있었어도(같은 시각), 문자열이 아니라 파싱한
    # datetime으로 비교하므로 정확히 매칭돼야 한다. 존재하지 않는 시각은 안 나온다.
    assert res.json()["existing"] == ["2026-07-01T10:00:00Z"]


async def test_rivalries_pairwise_counts(client):
    """상성(1:1 상대전적) — 시드 3경기(p1 승 / p2 승 / 무)가 한 쌍으로 정확히 집계된다."""
    p1 = await _signup(client, "rival01", "RivalA#2001")
    await _signup(client, "rival02", "RivalB#2002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    await _create_match(
        client, headers, "2026-07-01",
        team1=[_slot("rival01", "테란")], team2=[_slot("rival02", "저그")], result="team1",
    )
    await _create_match(
        client, headers, "2026-07-02",
        team1=[_slot("rival02", "저그")], team2=[_slot("rival01", "테란")], result="team1",
    )
    await _create_match(
        client, headers, "2026-07-03",
        team1=[_slot("rival01", "테란")], team2=[_slot("rival02", "저그")], result="draw",
    )

    res = await client.get("/api/game-results/stats/rivalries", headers=headers)
    assert res.status_code == 200, res.text
    pairs = [p for p in res.json()["pairs"] if {p["a"], p["b"]} == {"rival01", "rival02"}]
    assert len(pairs) == 1
    pair = pairs[0]
    wins = {pair["a"]: pair["aWins"], pair["b"]: pair["bWins"]}
    assert wins["rival01"] == 1
    assert wins["rival02"] == 1
    assert pair["draws"] == 1

    # 기간 필터 — 첫 경기만 잡히는 범위로 좁히면 승수도 그만큼만.
    res = await client.get(
        "/api/game-results/stats/rivalries", headers=headers,
        params={"dateFrom": "2026-07-01", "dateTo": "2026-07-01"},
    )
    pairs = [p for p in res.json()["pairs"] if {p["a"], p["b"]} == {"rival01", "rival02"}]
    assert len(pairs) == 1
    wins = {pairs[0]["a"]: pairs[0]["aWins"], pairs[0]["b"]: pairs[0]["bWins"]}
    assert wins["rival01"] == 1
    assert wins["rival02"] == 0
    assert pairs[0]["draws"] == 0


async def test_rivalries_team_mode_individualizes(client):
    """상성 팀전 모드 — 2:2 팀전이 반대 팀 회원 조합 4쌍으로 개인화되고,
    solo 모드에는 팀전이 전혀 안 섞인다."""
    t1 = await _signup(client, "teamriv01", "TeamRivA#3001")
    await _signup(client, "teamriv02", "TeamRivB#3002")
    await _signup(client, "teamriv03", "TeamRivC#3003")
    await _signup(client, "teamriv04", "TeamRivD#3004")
    headers = {"Authorization": f"Bearer {t1['accessToken']}"}

    # (01,02) vs (03,04) — team1 승 2번, team2 승 1번.
    for date, result in [("2026-07-01", "team1"), ("2026-07-02", "team1"), ("2026-07-03", "team2")]:
        await _create_match(
            client, headers, date,
            team1=[_slot("teamriv01", "테란"), _slot("teamriv02", "저그")],
            team2=[_slot("teamriv03", "프로토스"), _slot("teamriv04", "테란")],
            result=result, match_type="0102",
        )

    res = await client.get(
        "/api/game-results/stats/rivalries", headers=headers, params={"mode": "team"},
    )
    assert res.status_code == 200, res.text
    ours = {"teamriv01", "teamriv02", "teamriv03", "teamriv04"}
    pairs = [p for p in res.json()["pairs"] if {p["a"], p["b"]} <= ours]
    # 반대 팀 조합만 4쌍 — 같은 팀(01-02, 03-04) 쌍은 안 생긴다.
    assert {frozenset((p["a"], p["b"])) for p in pairs} == {
        frozenset(("teamriv01", "teamriv03")), frozenset(("teamriv01", "teamriv04")),
        frozenset(("teamriv02", "teamriv03")), frozenset(("teamriv02", "teamriv04")),
    }
    for p in pairs:
        wins = {p["a"]: p["aWins"], p["b"]: p["bWins"]}
        team1_side = p["a"] if p["a"] in ("teamriv01", "teamriv02") else p["b"]
        team2_side = p["a"] if team1_side == p["b"] else p["b"]
        assert wins[team1_side] == 2
        assert wins[team2_side] == 1
        assert p["draws"] == 0

    # solo 모드(기본)에는 팀전이 안 섞인다.
    res = await client.get("/api/game-results/stats/rivalries", headers=headers)
    assert not [p for p in res.json()["pairs"] if {p["a"], p["b"]} <= ours]
