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
        "plays": 3, "wins": 1, "losses": 1, "draws": 1, "winRate": 33.3, "bests": 0, "lostBests": 0,
        "avgApm": 110, "avgEapm": 85, "avgCmd": 52, "avgEcmd": 41, "avgBuild": 32,
        # 생산 구성은 리플레이로 등록한 경기만 실린다 — 이 픽스처는 수기 등록이라 없다.
        "buildMix": None, "avgWorker5": None, "mixPlays": None, "mixSeconds": None,
        "upPlays": None,
        # 칭호 재료(요청) — 요약도 맵 이름도 없는 수기 등록 픽스처라 둘 다 빈 사전이다.
        "tactics": {}, "maps": {},
    }
    assert by_id["player01"]["byRace"]["테란"] == {
        "plays": 2, "wins": 1, "losses": 0, "draws": 1, "winRate": 50.0, "bests": 0, "lostBests": 0,
        "avgApm": 100, "avgEapm": 80, "avgCmd": 50, "avgEcmd": 40, "avgBuild": 30,
        "buildMix": None, "avgWorker5": None, "mixPlays": None, "mixSeconds": None,
        "upPlays": None,
        # 칭호 재료(요청) — 요약도 맵 이름도 없는 수기 등록 픽스처라 둘 다 빈 사전이다.
        "tactics": {}, "maps": {},
    }
    assert by_id["player01"]["byRace"]["프로토스"] == {
        "plays": 1, "wins": 0, "losses": 1, "draws": 0, "winRate": 0.0, "bests": 0, "lostBests": 0,
        "avgApm": 120, "avgEapm": 90, "avgCmd": 55, "avgEcmd": 42, "avgBuild": 34,
        "buildMix": None, "avgWorker5": None, "mixPlays": None, "mixSeconds": None,
        "upPlays": None,
        # 칭호 재료(요청) — 요약도 맵 이름도 없는 수기 등록 픽스처라 둘 다 빈 사전이다.
        "tactics": {}, "maps": {},
    }
    assert by_id["player01"]["byRace"]["저그"]["plays"] == 0
    assert by_id["player01"]["mostPlayedRace"] == "테란"  # 2판 > 1판

    # player02: (200+240)/2경기=220
    p2_overall = by_id["player02"]["overall"]
    assert p2_overall == {
        "plays": 3, "wins": 1, "losses": 1, "draws": 1, "winRate": 33.3, "bests": 0, "lostBests": 0,
        "avgApm": 70, "avgEapm": 55, "avgCmd": 32, "avgEcmd": 22, "avgBuild": 16,
        "buildMix": None, "avgWorker5": None, "mixPlays": None, "mixSeconds": None,
        "upPlays": None,
        # 칭호 재료(요청) — 요약도 맵 이름도 없는 수기 등록 픽스처라 둘 다 빈 사전이다.
        "tactics": {}, "maps": {},
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
    assert overall["avgEcmd"] == 40
    by_race = res.json()["members"][0]["byRace"]["테란"]
    assert by_race["avgEapm"] == 80
    assert by_race["avgEcmd"] == 40


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
    판수가 개인전 두 판까지 내려오면서 서너 판만 뛴 회원이 순위표에 오르는데, 그 구간은
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
    assert overall["avgCmd"] == 150
    assert overall["avgEcmd"] == 120
    assert overall["avgBuild"] == 90


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
    # 생산은 10분당으로 환산된다(요청) — 2분짜리 한 판에서 300이면 10분당 1500이다.
    assert overall["avgBuild"] == 150


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


async def test_stats_shows_metrics_even_from_a_single_game(client):
    """판수가 적다고 지표를 가리지 않는다(요청: "그런 제한 다 없애줘 다 보여주기").

    한때는 개인전 2판·팀전 5판을 못 채우면 APM·커맨드·생산을 전부 null로 내렸다. 표에 "-"만
    늘어서는 값이 그 자체로 정보가 없었고, 적게 뛴 사람의 값은 적게 뛴 값으로 읽으면 된다.
    튀는 한 판을 걸러내는 장치(경기 길이·중앙값/MAD)는 그대로다 — 그쪽은 "몇 판 뛰었나"가
    아니라 "이 판이 정상인가"를 본다."""
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    await _solo_matches(client, headers, 1)
    overall = (await _solo_stats(client, headers))["overall"]
    assert overall["plays"] == 1
    assert [overall[f] for f in _METRIC_FIELDS] == [100, 80, 50, 40, 30]

    # 종족을 걸어 한 판만 남겨도 마찬가지다 — 칸마다 제 표본으로 보되 문턱은 없다.
    await _solo_matches(client, headers, 1, race="저그", start=2)
    zerg = (await _solo_stats(client, headers, race="저그"))["overall"]
    assert zerg["plays"] == 1
    assert zerg["avgApm"] == 100


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
    assert overall["avgCmd"] == 50
    assert overall["avgBuild"] == 30
    by_race = res.json()["members"][0]["byRace"]["테란"]
    assert by_race["avgCmd"] == 50
    assert by_race["avgBuild"] == 30


async def test_stats_counts_best_player_from_summary_data(client):
    """BEST PLAYER 횟수는 요약(summary_data)의 best가 그 참가자의 원본 게임 아이디와 같은
    경기를 센 값이다(요청: 통계 주요 지표에). 판정 자체는 프론트가 등록할 때 해 두고
    서버는 세기만 한다 — 여기서는 그 세는 일이 기간/유형/종족 필터를 그대로 타는지까지
    함께 본다."""
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    # 두 판 다 player01(테란) 승. 첫 판만 player01이, 둘째 판은 상대편 사람이 뽑혔다.
    for date, best in (("2026-07-01", "player01"), ("2026-07-02", "player02")):
        res = await client.post(
            "/api/game-results",
            headers=headers,
            json={
                "date": date, "note": "",
                "team1": [_slot("player01", "테란")],
                "team2": [_slot("player02", "저그")],
                "result": "team1",
                "summaryData": {"v": 2, "best": best, "beats": []},
            },
        )
        assert res.status_code == 200, res.text
    # 요약이 아예 없는 경기(수기 등록)는 아무에게도 안 세진다.
    await _create_match(
        client, headers, "2026-07-03",
        team1=[_slot("player01", "테란")], team2=[_slot("player02", "저그")], result="team1",
    )

    res = await client.get(
        "/api/game-results/stats", headers=headers, params={"memberIds": "player01,player02"}
    )
    by_id = {m["memberId"]: m for m in res.json()["members"]}
    assert by_id["player01"]["overall"]["bests"] == 1
    assert by_id["player02"]["overall"]["bests"] == 1
    # 종족별로도 갈린다 — player01은 테란으로만 뛰었다.
    assert by_id["player01"]["byRace"]["테란"]["bests"] == 1
    assert by_id["player01"]["byRace"]["저그"]["bests"] == 0

    # 기간을 첫 판만으로 좁히면 player02의 것은 빠진다.
    res = await client.get(
        "/api/game-results/stats", headers=headers,
        params={"memberIds": "player01,player02", "dateFrom": "2026-07-01", "dateTo": "2026-07-01"},
    )
    by_id = {m["memberId"]: m for m in res.json()["members"]}
    assert by_id["player01"]["overall"]["bests"] == 1
    assert by_id["player02"]["overall"]["bests"] == 0


async def test_stats_counts_legacy_mvp_key(client):
    """이름을 바꾸기 전에 저장된 요약은 같은 값을 mvp 키로 들고 있다(요청: MVP → BEST
    PLAYER). 이미 쌓인 경기를 다시 분석하지 않고도 그대로 세져야 한다 — 세는 쪽이 두 키를
    하나로 본다."""
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    res = await client.post(
        "/api/game-results",
        headers=headers,
        json={
            "date": "2026-07-01", "note": "",
            "team1": [_slot("player01", "테란")],
            "team2": [_slot("player02", "저그")],
            "result": "team1",
            "summaryData": {"v": 2, "mvp": "player02", "beats": []},
        },
    )
    assert res.status_code == 200, res.text

    res = await client.get(
        "/api/game-results/stats", headers=headers, params={"memberIds": "player01,player02"}
    )
    by_id = {m["memberId"]: m for m in res.json()["members"]}
    assert by_id["player02"]["overall"]["bests"] == 1
    assert by_id["player01"]["overall"]["bests"] == 0


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
        "plays": 1, "wins": 0, "losses": 1, "draws": 0, "winRate": 0.0, "bests": 0, "lostBests": 0,
        "avgApm": 120, "avgEapm": 90, "avgCmd": 55, "avgEcmd": 42, "avgBuild": 34,
        "buildMix": None, "avgWorker5": None, "mixPlays": None, "mixSeconds": None,
        "upPlays": None,
        # 칭호 재료(요청) — 요약도 맵 이름도 없는 수기 등록 픽스처라 둘 다 빈 사전이다.
        "tactics": {}, "maps": {},
    }
    # byRace/mostPlayedRace는 race 파라미터와 무관하게 항상 전체 종족 기준이어야 한다.
    assert res.json()["members"][0]["mostPlayedRace"] == "테란"


async def test_stats_member_with_zero_matches_returns_zero_defaults(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    res = await client.get("/api/game-results/stats", headers=headers, params={"memberIds": "player01"})
    entry = res.json()["members"][0]
    assert entry["overall"] == {
        "plays": 0, "wins": 0, "losses": 0, "draws": 0, "winRate": 0.0, "bests": 0, "lostBests": 0,
        "avgApm": None, "avgEapm": None, "avgCmd": None, "avgEcmd": None, "avgBuild": None,
        "buildMix": None, "avgWorker5": None, "mixPlays": None, "mixSeconds": None,
        "upPlays": None,
        # 칭호 재료(요청) — 요약도 맵 이름도 없는 수기 등록 픽스처라 둘 다 빈 사전이다.
        "tactics": {}, "maps": {},
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


def _mix(
    b_prod=0, b_def=0, u_basic=0, u_adv=0, u_caster=0, u_ground=0, u_air=0, worker5=0,
    up_gw=0, up_ga=0, up_aw=0, up_aa=0, up_sh=0, buildings=None, units=None, skills=None,
    building_secs=None, unit_secs=None, skill_secs=None, core_seconds=None, core_cmd=0,
    core_build=0, core_unit=0, ups=None, up_counts=None, skills_won=None,
) -> dict:
    return {
        "bProd": b_prod, "bDef": b_def,
        "uBasic": u_basic, "uAdv": u_adv, "uCaster": u_caster,
        "uGround": u_ground, "uAir": u_air, "worker5": worker5,
        "upGw": up_gw, "upGa": up_ga, "upAw": up_aw, "upAa": up_aa, "upSh": up_sh,
        # 업그레이드 줄별 합·분모(요청: 종족마다 줄이 달라 종족별로 보여준다) — 줄이 실린
        # 경기가 없으면 빈 사전이다.
        "ups": ups or {}, "upCounts": up_counts or {},
        "buildings": buildings or {}, "units": units or {}, "skills": skills or {},
        # 이긴 판에서만 센 마법 원장 — 칭호가 보는 값이다(요청). 집계에서만 채워진다.
        "skillsWon": skills_won or {},
        "buildingSecs": building_secs or {}, "unitSecs": unit_secs or {},
        "skillSecs": skill_secs or {},
        # 주요시간대(초)와 그 구간 안의 커맨드 수 — 도넛 옆 "분당 몇 채/몇 기"의 분모와
        # 분자다. 파서가 경기마다 재서 실어 보낸다. 위 도넛 구성비·Top5 원장은 경기 전체로
        # 세므로 자가 다르다(요청).
        "coreSeconds": core_seconds, "coreCmd": core_cmd,
        "coreBuild": core_build, "coreUnit": core_unit,
    }


async def test_stats_sums_build_mix_across_matches(client):
    """생산 구성(도넛 셋 + 초반 일꾼)은 기간 안의 경기를 통째로 더한다 — 경기마다 비율을
    내서 평균 내지 않는다(짧은 판 한 번이 그림을 흔들지 않게)."""
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    s1 = _slot("player01", "테란", 100, 80, 500, 400, build=300)
    s1["buildMix"] = _mix(
        b_prod=20, b_def=5, u_basic=60, u_adv=10, u_caster=2, u_ground=65, u_air=7,
        worker5=14, up_gw=3, up_ga=2, up_aw=1, up_aa=0,
        buildings={"Barracks": 4, "Bunker": 2}, units={"Marine": 40, "Siege Tank (Tank Mode)": 6},
        skills={"Stim Packs": 12}, core_seconds=600, core_build=9, core_unit=31,
    )
    s2 = _slot("player01", "테란", 100, 80, 500, 400, build=300)
    s2["buildMix"] = _mix(
        b_prod=10, b_def=15, u_basic=20, u_adv=30, u_caster=8, u_ground=40, u_air=18,
        worker5=8, up_gw=2, up_ga=1, up_aw=3, up_aa=2,
        buildings={"Barracks": 3, "Starport": 1}, units={"Marine": 25, "Wraith": 9},
        skills={"Stim Packs": 5, "Yamato Gun": 3}, core_seconds=600, core_build=7, core_unit=23,
    )
    await _create_match(
        client, headers, "2026-07-01",
        team1=[s1], team2=[_slot("player02", "저그")], result="team1", duration_seconds=1500,
    )
    await _create_match(
        client, headers, "2026-07-02",
        team1=[s2], team2=[_slot("player02", "저그")], result="team1", duration_seconds=1500,
    )
    # 최소 판수(개인전 2판)를 채워야 지표가 나온다 — 구성도 지표라 같은 문을 지난다.
    s3 = _slot("player01", "테란", 100, 80, 500, 400, build=300)
    await _create_match(
        client, headers, "2026-07-03",
        team1=[s3], team2=[_slot("player02", "저그")], result="team1", duration_seconds=1500,
    )

    res = await client.get("/api/game-results/stats", headers=headers, params={"memberIds": "player01"})
    overall = res.json()["members"][0]["overall"]
    assert overall["buildMix"] == _mix(
        b_prod=30, b_def=20, u_basic=80, u_adv=40, u_caster=10, u_ground=105, u_air=25,
        # 공/방 단계도 합계로 쌓인다 — 경기당 평균으로 되돌리는 나눗셈은 화면이 한다.
        worker5=22, up_gw=5, up_ga=3, up_aw=4, up_aa=2,
        # 건물·유닛·스킬 원장도 이름별로 더해진다(요청: 통계 Top5) — 순위를 매기고
        # 한국어로 옮기는 것은 화면의 몫이라 여기서는 합계만 확인한다.
        buildings={"Barracks": 7, "Bunker": 2, "Starport": 1},
        units={"Marine": 65, "Siege Tank (Tank Mode)": 6, "Wraith": 9},
        skills={"Stim Packs": 17, "Yamato Gun": 3},
        # 구간 커맨드 수도 합계로 쌓인다 — 도넛 옆 분당 값의 분자다.
        core_build=16, core_unit=54,
        # 이름별 분모는 '그 이름이 나온 경기들의 총 길이'다 — 주요시간대(600초)가 아니라
        # 경기 전체 길이(1500초)를 쓴다(요청: Top5는 전체 경기 기준이라 분자와 자를 맞춘다).
        # 두 판 다 1500초라 배럭은 3000, 첫 판에만 나온 벙커는 1500이다.
        building_secs={"Barracks": 3000, "Bunker": 1500, "Starport": 1500},
        unit_secs={"Marine": 3000, "Siege Tank (Tank Mode)": 1500, "Wraith": 1500},
        skill_secs={"Stim Packs": 3000, "Yamato Gun": 1500},
        # 셋 다 이긴 판이라(result=team1) 칭호용 원장도 위 skills와 같은 수다 — 진 판이
        # 섞이면 그만큼 적어진다(요청: 기술도 이긴 판만 센다).
        skills_won={"Stim Packs": 17, "Yamato Gun": 3},
    )
    # 두 분모도 함께 내려간다 — 세 판 중 두 판에만 구성이 실렸고, 그 두 판의 주요시간대는
    # 600초씩이다(경기 길이 900초와 다르다 — 분당 지표의 분모는 주요시간대 쪽이다).
    assert overall["mixPlays"] == 2
    assert overall["mixSeconds"] == 1200
    # 공/방/실드만의 분모 — 두 판 다 25분이라 20분 문턱을 넘어 둘 다 셌다.
    assert overall["upPlays"] == 2
    # 구성이 실린 경기는 둘뿐이다 — 없는 경기까지 세면 초반 일꾼이 실제보다 낮아진다.
    assert overall["avgWorker5"] == 11.0


async def test_stats_upgrade_average_skips_short_and_dataless_matches(client):
    """공/방/실드 평균은 '충분히 긴 경기'만 센다.

    3단계까지 올리는 데 필요한 연구 시간만 11분이 넘어서, 짧은 판은 구조적으로 3이 될 수
    없다 — 그런 판을 분모에 넣으면 평균이 실제보다 낮게 나온다(지적: 공방업이 너무 낮게
    나온다). 업그레이드 값을 아예 안 실은 옛 기록을 빼는 규칙은 아래 단위 테스트에서 본다
    (지금 저장 경로는 스키마 기본값 0을 채우므로 API로는 그 상태를 만들 수 없다)."""
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    # (1) 25분 — 세는 판. 3-3까지 올렸다.
    long_slot = _slot("player01", "테란", 100, 80, 500, 400, build=300)
    long_slot["buildMix"] = _mix(b_prod=10, up_gw=3, up_ga=3, core_seconds=600)
    await _create_match(
        client, headers, "2026-07-01",
        team1=[long_slot], team2=[_slot("player02", "저그")], result="team1", duration_seconds=1500,
    )
    # (2) 10분 — 짧아서 안 센다. 여기 값이 분모에 얹히면 평균이 절반으로 깎인다.
    short_slot = _slot("player01", "테란", 100, 80, 500, 400, build=300)
    short_slot["buildMix"] = _mix(b_prod=10, up_gw=0, up_ga=0, core_seconds=300)
    await _create_match(
        client, headers, "2026-07-02",
        team1=[short_slot], team2=[_slot("player02", "저그")], result="team1", duration_seconds=600,
    )
    # (3) 표본 문턱(개인전 2판)을 채우는 판 하나 — 25분이지만 업그레이드는 안 올렸다.
    plain = _slot("player01", "테란", 100, 80, 500, 400, build=300)
    plain["buildMix"] = _mix(b_prod=10, core_seconds=600)
    await _create_match(
        client, headers, "2026-07-03",
        team1=[plain], team2=[_slot("player02", "저그")], result="team1", duration_seconds=1500,
    )

    res = await client.get("/api/game-results/stats", headers=headers, params={"memberIds": "player01"})
    overall = res.json()["members"][0]["overall"]
    # 구성 자체는 세 판 다 실렸다 — 도넛·Top5의 분모는 그대로 셋이다.
    assert overall["mixPlays"] == 3
    # 그러나 공/방은 20분을 넘긴 두 판만 센다 — 10분짜리 (2)는 분모에서 빠진다.
    # 그 판까지 세면 3+0+0을 셋으로 나눠 1.0이 되어 실제(1.5)보다 낮게 나온다.
    assert overall["upPlays"] == 2
    assert overall["buildMix"]["upGw"] == 3
    assert overall["buildMix"]["upGa"] == 3


def test_build_mix_agg_skips_records_without_upgrade_keys():
    """업그레이드 값을 아예 안 실은 옛 기록은 공/방 분모에서 뺀다 — 0으로 세면 평균이
    그만큼 깎인다(실측: 한 사람의 열네 판 중 여섯 판이 그런 기록이었다). 지금 저장 경로는
    스키마 기본값으로 0을 채우므로, 그 시절 모양을 직접 만들어 집계 함수에 넣어 본다."""
    from app.domain.game_results.service import _build_mix_agg

    class _Row:
        def __init__(self, mix, dur):
            self.build_mix = mix
            self.duration_seconds = dur

    full = {"b_prod": 10, "core_seconds": 600, "up_gw": 3, "up_ga": 3, "up_aw": 0, "up_aa": 0, "up_sh": 0}
    legacy = {"b_prod": 10, "core_seconds": 600}  # 그 시절엔 up_* 키 자체가 없었다
    out = _build_mix_agg([_Row(full, 1500), _Row(legacy, 1500)])
    assert out["mix_plays"] == 2      # 도넛·Top5는 두 판 다 센다
    assert out["up_plays"] == 1       # 공/방은 값을 실은 한 판만
    assert out["build_mix"].up_gw == 3


async def test_stats_build_mix_is_none_without_replay_matches(client):
    """구성이 실린 경기가 하나도 없으면 None이다 — 0이 아니다(잰 적이 없다는 뜻)."""
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}
    for i in range(3):
        await _create_match(
            client, headers, f"2026-07-0{i + 1}",
            team1=[_slot("player01", "테란", 100, 80, 500, 400, build=300)],
            team2=[_slot("player02", "저그")], result="team1", duration_seconds=600,
        )
    res = await client.get("/api/game-results/stats", headers=headers, params={"memberIds": "player01"})
    overall = res.json()["members"][0]["overall"]
    assert overall["buildMix"] is None
    assert overall["avgWorker5"] is None


async def test_stats_counts_tactics_and_map_records(client):
    """전술 횟수(tactics)와 맵별 전적(maps) — 통계 화면의 칭호가 쓰는 두 재료다(요청:
    자막에서 강조되는 옆탱·센포 같은 것들과 '○○의 지배자'를 칭호로).

    세는 규칙 셋을 함께 못 박는다.
      - 한 beat에 같은 사람이 who와 who2로 두 번 실려도 한 번만 센다(옆탱처럼 '누구
        기지에서 했나'가 같이 적히는 문장이 있다).
      - 당한 쪽(whom)은 안 센다 — 칭호는 그 사람이 한 일로만 지어야 한다.
      - 요약이 없는 경기(수기 등록)는 아무 전술도 안 남기지만, 맵 전적에는 맵 이름이 있는
        한 그대로 들어간다(맵은 요약이 아니라 경기 자체의 사실이다).
      - 진 판의 수는 안 센다(요청: 전략·전술 칭호는 그 판을 이겼어야 인정) — 같은 센포라도
        won=False면 통계에 안 남는다.
    """
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    async def _post(date: str, map_name: str, result: str, beats: list[dict]) -> None:
        res = await client.post(
            "/api/game-results",
            headers=headers,
            json={
                "date": date, "note": "",
                "team1": [_slot("player01", "테란")],
                "team2": [_slot("player02", "프로토스")],
                "result": result,
                "mapName": map_name,
                "summaryData": {"v": 2, "beats": beats},
            },
        )
        assert res.status_code == 200, res.text

    await _post("2026-07-01", "로스트템플", "team1", [
        {"k": "side-tank", "won": True, "who": ["player01"]},
        {"k": "center-photon", "won": False, "who": ["player02"]},
    ])
    # who와 who2에 같은 사람 — 한 번만 세야 한다.
    await _post("2026-07-02", "로스트템플", "team1", [
        {"k": "side-tank", "won": True, "who": ["player01"], "who2": ["player01"]},
    ])
    # 당한 쪽(whom)은 player01이지만 이 전술을 한 사람은 player02다.
    await _post("2026-07-03", "헌터스", "team2", [
        {"k": "cannon-rush", "won": True, "who": ["player02"], "whom": ["player01"]},
        # 진 판이어도 세는 수(요청) — 셋방살이·노엘은 밀린 뒤에야 나오는 이야기라 이긴 판만
        # 보면 영영 안 잡힌다.
        {"k": "lodging", "won": False, "who": ["player01"]},
    ])

    res = await client.get(
        "/api/game-results/stats", headers=headers, params={"memberIds": "player01,player02"}
    )
    by_id = {m["memberId"]: m for m in res.json()["members"]}
    assert by_id["player01"]["overall"]["tactics"] == {"side-tank": 2, "lodging": 1}
    # 센포는 진 판(won=False)이라 안 세고, 포토러시만 남는다.
    assert by_id["player02"]["overall"]["tactics"] == {"cannon-rush": 1}
    # 종족별로도 갈린다 — player01은 테란으로만 뛰었다.
    assert by_id["player01"]["byRace"]["테란"]["tactics"] == {"side-tank": 2, "lodging": 1}
    assert by_id["player01"]["byRace"]["저그"]["tactics"] == {}

    # 맵 전적 — [판수, 승수].
    assert by_id["player01"]["overall"]["maps"] == {"로스트템플": [2, 2], "헌터스": [1, 0]}
    assert by_id["player02"]["overall"]["maps"] == {"로스트템플": [2, 0], "헌터스": [1, 1]}

    # 기간을 좁히면 둘 다 그 조건만 본다.
    res = await client.get(
        "/api/game-results/stats", headers=headers,
        params={"memberIds": "player01,player02", "dateFrom": "2026-07-03", "dateTo": "2026-07-03"},
    )
    by_id = {m["memberId"]: m for m in res.json()["members"]}
    assert by_id["player01"]["overall"]["tactics"] == {"lodging": 1}
    assert by_id["player01"]["overall"]["maps"] == {"헌터스": [1, 0]}
    assert by_id["player02"]["overall"]["tactics"] == {"cannon-rush": 1}


async def test_map_records_group_by_minimap_image_name(client):
    """맵 전적은 미니맵 관리에서 묶은 이름으로 센다(지적: 이름만 다른 같은 맵이 따로 나온다).

    빠른무한 계열처럼 판본·파일이름만 다른 맵이 여러 벌이라, 리플레이에 적힌 이름으로 세면
    같은 맵이 여러 갈래로 쪼개져 어느 것도 문턱을 못 넘는다. 격자(replay_maps)가 가리키는
    미니맵 그림 이름이 곧 운영자가 부르는 맵 이름이고, 그림이 없는 맵만 리플레이 이름으로
    받는다.
    """
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    # 이름이 다른 두 맵을 각각 제 격자와 함께 올린다 — 격자가 다르면 해시도 다르다.
    grids = {
        "빠른무한": {"hash": "aa11aa11", "name": "빠른무한", "width": 2, "height": 2,
                  "palette": [0, 1], "tiles": "AAEBAA==", "resources": []},
        "Super빠른무한": {"hash": "bb22bb22", "name": "Super빠른무한", "width": 2, "height": 2,
                       "palette": [0, 1], "tiles": "AQABAQ==", "resources": []},
    }
    for i, (map_name, grid) in enumerate(grids.items()):
        res = await client.post("/api/game-results", headers=headers, json={
            "date": f"2026-07-0{i + 1}", "note": "",
            "team1": [_slot("player01", "테란")], "team2": [_slot("player02", "저그")],
            "result": "team1", "mapName": map_name, "mapData": grid,
        })
        assert res.status_code == 200, res.text

    # 아직 묶기 전 — 리플레이에 적힌 이름 그대로 둘로 나뉜다.
    res = await client.get("/api/game-results/stats", headers=headers, params={"memberIds": "player01"})
    assert res.json()["members"][0]["overall"]["maps"] == {"빠른무한": [1, 1], "Super빠른무한": [1, 1]}

    # 미니맵 그림 한 장에 두 격자를 함께 묶는다(운영자가 미니맵 메뉴에서 하는 일).
    res = await client.post("/api/game-results/replay-maps/images", headers=headers, json={
        "name": "빨무", "image": "data:image/png;base64,AAAA",
        "hashes": ["aa11aa11", "bb22bb22"],
    })
    assert res.status_code in (200, 201), res.text

    res = await client.get("/api/game-results/stats", headers=headers, params={"memberIds": "player01"})
    assert res.json()["members"][0]["overall"]["maps"] == {"빨무": [2, 2]}


async def test_stats_serves_won_only_block(client):
    """이긴 판만 놓고 낸 값 한 벌(won) — 칭호가 '무엇으로 판을 풀었나'를 물을 때 쓴다(요청).

    같은 사람이 이긴 판과 진 판에서 다른 구성을 뽑았으면, overall은 둘을 다 담고 won은
    이긴 판 것만 담아야 한다.
    """
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    win = _slot("player01", "테란", 100, 80, 500, 400, build=300)
    win["buildMix"] = {"uGround": 40, "uAir": 0, "skills": {"Yamato Gun": 2}}
    lose = _slot("player01", "테란", 100, 80, 500, 400, build=300)
    lose["buildMix"] = {"uGround": 0, "uAir": 60, "skills": {"Yamato Gun": 5}}

    await _create_match(
        client, headers, "2026-07-01",
        team1=[win], team2=[_slot("player02", "저그")], result="team1", duration_seconds=1500,
    )
    await _create_match(
        client, headers, "2026-07-02",
        team1=[lose], team2=[_slot("player02", "저그")], result="team2", duration_seconds=1500,
    )

    res = await client.get("/api/game-results/stats", headers=headers, params={"memberIds": "player01"})
    entry = res.json()["members"][0]
    # 전체는 둘을 다 담는다 — 화면의 도넛·Top5가 쓰는 값이다.
    assert entry["overall"]["buildMix"]["uGround"] == 40
    assert entry["overall"]["buildMix"]["uAir"] == 60
    # 이긴 판만 놓으면 그 판의 구성만 남는다.
    assert entry["won"]["buildMix"]["uGround"] == 40
    assert entry["won"]["buildMix"]["uAir"] == 0
    # 마법 원장도 마찬가지다(이긴 판의 두 번만).
    assert entry["won"]["buildMix"]["skills"] == {"Yamato Gun": 2}


async def test_stats_counts_bests_in_lost_games(client):
    """진 판에서 뽑힌 BEST(요청: 졌잘싸 퀸) — 판을 가장 많이 만들고도 진 자리다.

    같은 사람이 이긴 판과 진 판에서 각각 BEST였으면 bests는 둘, lostBests는 하나다.
    """
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    for date, result in (("2026-07-01", "team1"), ("2026-07-02", "team2")):
        res = await client.post(
            "/api/game-results", headers=headers,
            json={
                "date": date, "note": "",
                "team1": [_slot("player01", "테란")],
                "team2": [_slot("player02", "프로토스")],
                "result": result,
                "summaryData": {"v": 2, "beats": [], "best": "player01"},
            },
        )
        assert res.status_code == 200, res.text

    res = await client.get(
        "/api/game-results/stats", headers=headers, params={"memberIds": "player01"}
    )
    overall = res.json()["members"][0]["overall"]
    assert overall["bests"] == 2
    assert overall["lostBests"] == 1
