"""POST /api/game-results/merge-replay — 이미 등록된 경기에 리플레이 내부 정보만 다시 덮어쓰는
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

    create = await client.post("/api/game-results", headers=headers, json={
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
    merge = await client.post("/api/game-results/merge-replay", headers=headers, json={
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

    got = (await client.get(f"/api/game-results/{match_id}", headers=headers)).json()
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

    create = await client.post("/api/game-results", headers=headers, json={
        "date": "2026-07-02",
        "team1": [{"memberId": "player01", "race": "테란", "playerName": "player01"}],
        "team2": [{"memberId": "player02", "race": "저그", "playerName": "player02"}],
        "result": "team1", "gameStartedAt": gsa,
    })
    assert create.status_code == 200, create.text
    mid = create.json()["id"]

    # result를 team2로 확실히 덮어쓰기.
    merge = await client.post("/api/game-results/merge-replay", headers=headers, json={
        "gameStartedAt": gsa, "result": "team2",
        "players": [{"playerName": "player01"}, {"playerName": "player02"}],
    })
    assert merge.status_code == 200, merge.text
    got = (await client.get(f"/api/game-results/{mid}", headers=headers)).json()
    assert got["result"] == "team2"


_REP_DATA_URL = "data:application/octet-stream;base64,QUJD"  # 'ABC' — 내용은 아무 바이트나 OK


async def test_replay_filename_new_format_and_merge_updates_it(client):
    """리플레이 다운로드 파일명 = [경기번호] 팀1로스터 VS 팀2로스터 (맵 특수문자 제거).rep.
    중복 리플레이 재등록(merge) 시 파일명을 신규 포맷으로 갱신한다(요청)."""
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}
    gsa = "2026-07-05T03:00:00+00:00"

    create = await client.post("/api/game-results", headers=headers, json={
        "date": "2026-07-05",
        "team1": [{"memberId": "player01", "race": "테란", "playerName": "player01"}],
        "team2": [{"memberId": "player02", "race": "저그", "playerName": "player02"}],
        "result": "team1", "gameStartedAt": gsa, "mapName": "Poly(o)id!",
        "replay": {"originalName": "aaa.rep", "displayName": "aaa.rep", "url": _REP_DATA_URL},
    })
    assert create.status_code == 200, create.text
    match = create.json()
    match_no = match["matchNo"]
    # 맵의 특수문자(!)만 삭제되고 일반 문장기호(괄호)는 남는다(요청). displayName은 서버가 만든다.
    assert match["replay"]["displayName"] == f"[{match_no}] player01 VS player02 (Poly(o)id).rep", (
        match["replay"]["displayName"]
    )

    # 같은 게임시각(중복)으로 다시 올리면 파일명을 신규 포맷으로 갱신 — 맵이 바뀌면 이름도 따라간다.
    # 아포스트로피(')와 앰퍼샌드(&)는 일반 기호라 유지, *는 특수문자라 제거된다.
    merge = await client.post("/api/game-results/merge-replay", headers=headers, json={
        "gameStartedAt": gsa, "result": None, "mapName": "Gaia's & Sylph*",
        "players": [{"playerName": "player01"}, {"playerName": "player02"}],
    })
    assert merge.status_code == 200, merge.text
    got = (await client.get(f"/api/game-results/{match['id']}", headers=headers)).json()
    assert got["replay"]["displayName"] == f"[{match_no}] player01 VS player02 (Gaia's & Sylph).rep", (
        got["replay"]["displayName"]
    )


async def test_summary_is_stored_and_backfilled_by_merge(client):
    """리플레이에서 만든 전황 요약이 등록 때 저장되고, 요약이 없던 예전 경기도 리플레이를
    다시 올리면(머지) 채워진다(요청: "일단 요약 등록", "배치 업로드에서 갱신").

    저장되는 건 완성된 문장이 아니라 구조화된 데이터다 — 닉네임이나 문구가 바뀌어도 보는
    시점의 값으로 읽히게 하기 위해서다(요청). 서버는 이걸 해석하지 않고 그대로 보관한다."""
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    def body(gsa: str, summary: dict | None) -> dict:
        return {
            "date": "2026-07-01",
            "team1": [{"memberId": "player01", "race": "테란", "playerName": "player01"}],
            "team2": [{"memberId": "player02", "race": "저그", "playerName": "player02"}],
            "result": "team1", "gameStartedAt": gsa, "summaryData": summary,
        }

    # 등록 때 요약이 그대로 저장된다.
    with_sum = {
        "v": 1,
        "beats": [
            {"k": "bionic", "won": True, "who": ["player01"], "at": 4200, "p": {"tank": True}},
            {"k": "result", "won": True, "who": ["player01"], "p": {"mode": "plain"}},
        ],
    }
    a = await client.post("/api/game-results", headers=headers,
                          json=body("2026-07-01T03:00:00+00:00", with_sum))
    assert a.status_code == 200, a.text
    assert a.json()["summaryData"] == with_sum

    # 요약 없이 등록된 예전 경기.
    gsa = "2026-07-01T04:00:00+00:00"
    b = await client.post("/api/game-results", headers=headers, json=body(gsa, None))
    assert b.status_code == 200, b.text
    old_id = b.json()["id"]
    assert b.json()["summaryData"] is None

    # 리플레이 재등록(머지)으로 요약만 백필된다.
    backfilled = {"v": 1, "beats": [{"k": "recall", "won": True, "who": ["player01"], "at": 900}]}
    merge = await client.post("/api/game-results/merge-replay", headers=headers, json={
        "gameStartedAt": gsa, "result": None, "summaryData": backfilled, "players": [],
    })
    assert merge.status_code == 200, merge.text
    assert merge.json()["merged"] is True
    got = (await client.get(f"/api/game-results/{old_id}", headers=headers)).json()
    assert got["summaryData"] == backfilled

    # 요약을 못 만든 머지(summaryData=None)는 기존 값을 지우지 않는다.
    again = await client.post("/api/game-results/merge-replay", headers=headers, json={
        "gameStartedAt": gsa, "result": None, "summaryData": None, "players": [],
    })
    assert again.status_code == 200, again.text
    got2 = (await client.get(f"/api/game-results/{old_id}", headers=headers)).json()
    assert got2["summaryData"] == backfilled


async def test_merge_no_matching_game_returns_false(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}
    merge = await client.post("/api/game-results/merge-replay", headers=headers, json={
        "gameStartedAt": "2099-01-01T00:00:00+00:00", "result": None, "players": [],
    })
    assert merge.status_code == 200, merge.text
    assert merge.json()["merged"] is False
