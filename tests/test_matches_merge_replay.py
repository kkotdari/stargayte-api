"""POST /api/matches/merge-replay — 이미 등록된 경기에 리플레이 내부 정보만 다시 덮어쓰는
머지(요청: 중복 리플레이 재등록 시 새 컬럼 백필). 지표/맵/시간은 항상, 승패는 확실할 때만
갱신하고 경기번호·등록자·메모·참가자 회원연결은 보존하는지 검증한다."""


async def _signup(client, member_id: str, battletag: str) -> dict:
    res = await client.post(
        "/api/auth/signup",
        json={"id": member_id, "password": "pass1234", "battletag": battletag,
              "replayAliases": [member_id], "insta": ""},
    )
    assert res.status_code == 200, res.text
    return res.json()


async def test_merge_backfills_metrics_and_preserves_identity(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}
    gsa = "2026-07-01T03:00:00+00:00"

    create = await client.post("/api/matches", headers=headers, json={
        "date": "2026-07-01",
        "team1": [{"memberId": "player01", "race": "테란", "playerName": "player01",
                   "apm": 100, "eapm": 80, "cmdCount": 500, "effectiveCmdCount": 400}],
        "team2": [{"memberId": "player02", "race": "저그", "playerName": "player02",
                   "apm": 60, "eapm": 50, "cmdCount": 300, "effectiveCmdCount": 200}],
        "result": "team1",
        "gameStartedAt": gsa, "mapName": "Fighting Spirit", "durationSeconds": 600,
    })
    assert create.status_code == 200, create.text
    match = create.json()
    match_id, match_no = match["id"], match["matchNo"]
    assert match["team1"][0]["buildCount"] is None  # 생성 직후엔 생산 지표 없음

    # 머지 — 생산 백필 + 지표 갱신, 승패는 None(유지), 메모는 안 보냄.
    merge = await client.post("/api/matches/merge-replay", headers=headers, json={
        "gameStartedAt": gsa, "result": None, "mapName": "Fighting Spirit", "durationSeconds": 600,
        "players": [
            {"playerName": "player01", "race": "테란", "apm": 111, "eapm": 88,
             "cmdCount": 555, "effectiveCmdCount": 444, "buildCount": 300},
            {"playerName": "player02", "race": "저그", "apm": 66, "eapm": 55,
             "cmdCount": 333, "effectiveCmdCount": 222, "buildCount": 150},
        ],
    })
    assert merge.status_code == 200, merge.text
    assert merge.json() == {"merged": True, "matchNo": match_no}

    got = (await client.get(f"/api/matches/{match_id}", headers=headers)).json()
    t1 = got["team1"][0]
    assert t1["buildCount"] == 300            # 백필됨
    assert t1["apm"] == 111 and t1["effectiveCmdCount"] == 444  # 지표 갱신
    assert got["result"] == "team1"           # 승패 None이라 유지
    assert got["matchNo"] == match_no          # 경기번호 보존
    assert got["createdBy"]["id"] == "player01"  # 등록자 보존


async def test_merge_overwrites_result_only_when_provided(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}
    gsa = "2026-07-02T03:00:00+00:00"

    create = await client.post("/api/matches", headers=headers, json={
        "date": "2026-07-02",
        "team1": [{"memberId": "player01", "race": "테란", "playerName": "player01"}],
        "team2": [{"memberId": "player02", "race": "저그", "playerName": "player02"}],
        "result": "team1", "gameStartedAt": gsa,
    })
    assert create.status_code == 200, create.text
    mid = create.json()["id"]

    # result를 team2로 확실히 덮어쓰기.
    merge = await client.post("/api/matches/merge-replay", headers=headers, json={
        "gameStartedAt": gsa, "result": "team2",
        "players": [{"playerName": "player01"}, {"playerName": "player02"}],
    })
    assert merge.status_code == 200, merge.text
    got = (await client.get(f"/api/matches/{mid}", headers=headers)).json()
    assert got["result"] == "team2"


async def test_merge_no_matching_game_returns_false(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}
    merge = await client.post("/api/matches/merge-replay", headers=headers, json={
        "gameStartedAt": "2099-01-01T00:00:00+00:00", "result": None, "players": [],
    })
    assert merge.status_code == 200, merge.text
    assert merge.json()["merged"] is False
