"""GET /api/matches/stats, GET /api/matches/main-race, POST /api/matches/duplicate-check 검증.

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
    duration_seconds: int | None = None,
) -> dict:
    res = await client.post(
        "/api/matches",
        headers=headers,
        json={
            "date": date, "team1": team1, "team2": team2, "result": result, "note": "",
            "durationSeconds": duration_seconds,
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

    res = await client.get("/api/matches/stats", headers=headers, params={"memberIds": "player01,player02"})
    assert res.status_code == 200, res.text
    by_id = {m["memberId"]: m for m in res.json()["members"]}

    # avgEcmd는 총합이 아니라 "분당" 값이다 — match1/match2 둘 다 10분(600초)짜리라
    # (400+420)/(20분)=41, 테란만이면 400/10분=40, 프로토스만이면 420/10분=42.
    p1_overall = by_id["player01"]["overall"]
    assert p1_overall == {
        "plays": 3, "wins": 1, "losses": 1, "draws": 1, "winRate": 33.3,
        "avgApm": 110, "avgEapm": 85, "avgCmd": 525, "avgEcmd": 41, "avgBuild": 320,
    }
    assert by_id["player01"]["byRace"]["테란"] == {
        "plays": 2, "wins": 1, "losses": 0, "draws": 1, "winRate": 50.0,
        "avgApm": 100, "avgEapm": 80, "avgCmd": 500, "avgEcmd": 40, "avgBuild": 300,
    }
    assert by_id["player01"]["byRace"]["프로토스"] == {
        "plays": 1, "wins": 0, "losses": 1, "draws": 0, "winRate": 0.0,
        "avgApm": 120, "avgEapm": 90, "avgCmd": 550, "avgEcmd": 42, "avgBuild": 340,
    }
    assert by_id["player01"]["byRace"]["저그"]["plays"] == 0
    assert by_id["player01"]["mostPlayedRace"] == "테란"  # 2판 > 1판

    # player02: (200+240)/(20분)=22
    p2_overall = by_id["player02"]["overall"]
    assert p2_overall == {
        "plays": 3, "wins": 1, "losses": 1, "draws": 1, "winRate": 33.3,
        "avgApm": 70, "avgEapm": 55, "avgCmd": 325, "avgEcmd": 22, "avgBuild": 165,
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

    res = await client.get("/api/matches/stats", headers=headers, params={"memberIds": "player01"})
    overall = res.json()["members"][0]["overall"]
    assert overall["plays"] == 6  # 전적 자체는 이상치 경기도 포함해서 그대로 6전
    # 이상치를 뺀 나머지 5경기만으로 평균 -> eapm 80, ecmd 분당 (400+410+390+405+395)/50분=40
    assert overall["avgEapm"] == 80
    assert overall["avgEcmd"] == 40
    by_race = res.json()["members"][0]["byRace"]["테란"]
    assert by_race["avgEapm"] == 80
    assert by_race["avgEcmd"] == 40


async def test_stats_race_filter_scopes_overall(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}
    await _seed_matches(client, headers)

    res = await client.get(
        "/api/matches/stats", headers=headers, params={"memberIds": "player01", "race": "프로토스"}
    )
    overall = res.json()["members"][0]["overall"]
    assert overall == {
        "plays": 1, "wins": 0, "losses": 1, "draws": 0, "winRate": 0.0,
        "avgApm": 120, "avgEapm": 90, "avgCmd": 550, "avgEcmd": 42, "avgBuild": 340,
    }
    # byRace/mostPlayedRace는 race 파라미터와 무관하게 항상 전체 종족 기준이어야 한다.
    assert res.json()["members"][0]["mostPlayedRace"] == "테란"


async def test_stats_member_with_zero_matches_returns_zero_defaults(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    res = await client.get("/api/matches/stats", headers=headers, params={"memberIds": "player01"})
    entry = res.json()["members"][0]
    assert entry["overall"] == {
        "plays": 0, "wins": 0, "losses": 0, "draws": 0, "winRate": 0.0,
        "avgApm": None, "avgEapm": None, "avgCmd": None, "avgEcmd": None, "avgBuild": None,
    }
    assert entry["mostPlayedRace"] is None


async def test_main_race_picks_most_played(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}
    await _seed_matches(client, headers)

    res = await client.get("/api/matches/main-race", headers=headers, params={"memberId": "player01"})
    assert res.status_code == 200, res.text
    assert res.json() == {"race": "테란"}


async def test_duplicate_check_matches_regardless_of_timestamp_format(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    await client.post(
        "/api/matches",
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
        "/api/matches/duplicate-check",
        headers=headers,
        json={"gameStartedAt": ["2026-07-01T10:00:00Z", "2026-07-02T10:00:00Z"]},
    )
    assert res.status_code == 200, res.text
    # "Z"로 보냈지만 실제 저장은 "+00:00"으로 돼 있었어도(같은 시각), 문자열이 아니라 파싱한
    # datetime으로 비교하므로 정확히 매칭돼야 한다. 존재하지 않는 시각은 안 나온다.
    assert res.json()["existing"] == ["2026-07-01T10:00:00Z"]
