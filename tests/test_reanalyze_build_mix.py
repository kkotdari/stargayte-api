"""재분석이 참가자의 생산 구성(build_mix)까지 갱신하는지(POST /api/game-results/{id}/summary).

"자막에는 핵이 나오는데 통계 스킬 칸에는 안 나온다"는 지적을 좇다가 남긴 시험이다.
생산 구성은 참가자 행마다 붙고, 짝은 리플레이 원본
게임 아이디(rawName)로 맞춘다 — 그 짝이 어긋나면 자막만 새것이 되고 수치는 옛것으로 남는다.
여기서 지키는 것: 재분석 payload의 slots가 통계의 스킬 원장까지 실제로 바꾼다."""


async def _signup(client, member_id: str, battletag: str) -> dict:
    res = await client.post("/api/auth/signup", json={
        "id": member_id, "password": "pass1234", "battletag": battletag,
        "replayAliases": [member_id], "insta": "",
    })
    assert res.status_code == 200, res.text
    return res.json()


async def test_reanalyze_updates_build_mix_skills(client):
    a = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    h = {"Authorization": f"Bearer {a['accessToken']}"}

    res = await client.post("/api/game-results", headers=h, json={
        "date": "2026-08-01", "note": "",
        "team1": [{"memberId": "player01", "race": "테란", "apm": 100, "eapm": 80,
                   "cmdCount": 500, "effectiveCmdCount": 400, "buildCount": 300}],
        "team2": [{"memberId": "player02", "race": "저그", "apm": 60, "eapm": 50,
                   "cmdCount": 300, "effectiveCmdCount": 200, "buildCount": 150}],
        "result": "team1", "durationSeconds": 1800,
    })
    assert res.status_code == 200, res.text
    match_id = res.json()["id"]

    # 재분석 — 프론트가 보내는 모양 그대로(camelCase, rawName으로 짝짓기).
    res = await client.post(f"/api/game-results/{match_id}/summary", headers=h, json={
        "slots": [{
            "rawName": "player01", "race": "테란",
            "apm": 111, "eapm": 88, "cmdCount": 555, "effectiveCmdCount": 444, "buildCount": 333,
            "buildMix": {
                "bProd": 10, "bDef": 2, "uBasic": 30, "uAdv": 10, "uCaster": 1,
                "uGround": 35, "uAir": 6, "worker5": 12,
                "upGw": 1, "upGa": 1, "upAw": 0, "upAa": 0, "upSh": 0,
                "ups": {}, "upCounts": {},
                "buildings": {"Barracks": 3}, "units": {"Marine": 30},
                "skills": {"Stim Packs": 26, "Nuclear Strike": 4},
                "buildingSecs": {}, "unitSecs": {}, "skillSecs": {},
                "coreSeconds": 900, "coreCmd": 400, "coreBuild": 100, "coreUnit": 200,
            },
        }],
    })
    assert res.status_code in (200, 204), res.text

    res = await client.get("/api/game-results/stats", headers=h,
                           params={"memberIds": "player01", "race": "테란"})
    mix = res.json()["members"][0]["overall"]["buildMix"]
    assert mix is not None, "재분석 뒤에도 생산 구성이 안 실렸다"
    assert mix["skills"].get("Nuclear Strike") == 4, mix["skills"]
