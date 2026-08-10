"""칭호 변경 알림(PUT /api/activities/epithets → 활동 목록의 notice 줄).

칭호를 뽑는 규칙은 화면에만 있고 서버는 '지금 값'을 받아 두었다가 달라진 것만 알림으로
남긴다(요청: 칭호 변경을 알림에 표시). 여기서 지키는 것:
  ① 처음 받은 칭호도 알림이 된다(없다가 생긴 것 — from은 없음)
  ② 같은 값을 다시 올리면 아무 일도 안 일어난다(알림이 겹쳐 쌓이지 않는다)
  ③ 바뀐 사람이 여럿이어도 알림은 한 줄이다
  ④ 그 줄이 활동 목록에 notice 종류로 실린다
"""


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


async def _report(client, headers, rows) -> int:
    res = await client.put("/api/activities/epithets", headers=headers, json={"epithets": rows})
    assert res.status_code == 200, res.text
    return res.json()["changed"]


async def _notices(client, headers) -> list[dict]:
    res = await client.get("/api/activities", headers=headers)
    assert res.status_code == 200, res.text
    return [it for it in res.json()["items"] if it["kind"] == "notice"]


async def test_epithet_change_becomes_one_notice(client):
    a = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {a['accessToken']}"}

    # ① 처음 받은 칭호 — 둘이 바뀌었지만 알림은 한 줄이다(③).
    assert await _report(client, headers, [
        {"memberId": "player01", "label": "핵보유국", "why": "사용 3회 — 클럽 1위"},
        {"memberId": "player02", "label": "드랍의 여신", "why": "자막에 잡힌 횟수 9회 — 클럽 1위"},
    ]) == 2
    notices = await _notices(client, headers)
    assert len(notices) == 1
    payload = notices[0]["notice"]["payload"]
    assert notices[0]["notice"]["kind"] == "epithet"
    changes = {c["memberId"]: c for c in payload["changes"]}
    assert changes["player01"]["from"] is None and changes["player01"]["to"] == "핵보유국"
    assert changes["player02"]["to"] == "드랍의 여신"

    # ② 같은 값을 다시 올리면 조용하다 — 근거 문구만 달라진 것도 마찬가지다.
    assert await _report(client, headers, [
        {"memberId": "player01", "label": "핵보유국", "why": "사용 5회 — 클럽 1위"},
        {"memberId": "player02", "label": "드랍의 여신", "why": "자막에 잡힌 횟수 9회 — 클럽 1위"},
    ]) == 0
    assert len(await _notices(client, headers)) == 1

    # 부르는 말이 바뀌면 그때는 알림이 하나 더 — from에 옛 칭호가 남는다.
    assert await _report(client, headers, [
        {"memberId": "player01", "label": "포토러시의 퀸", "why": "자막에 잡힌 횟수 4회 — 클럽 1위"},
    ]) == 1
    notices = await _notices(client, headers)
    assert len(notices) == 2
    latest = notices[0]["notice"]["payload"]["changes"]
    assert latest == [{
        "memberId": "player01", "from": "핵보유국", "to": "포토러시의 퀸",
        "why": "자막에 잡힌 횟수 4회 — 클럽 1위",
    }]


async def test_epithet_report_ignores_unknown_and_empty(client):
    """목록에 없는 회원은 그대로 두고, 모르는 회원·빈 칭호는 조용히 버린다.

    검색 등으로 일부만 계산된 화면이 남의 칭호를 지우면 안 된다 — 그래서 '없으면 삭제'가
    아니라 '없으면 그대로'다.
    """
    a = await _signup(client, "player01", "Shadow#1001")
    headers = {"Authorization": f"Bearer {a['accessToken']}"}
    assert await _report(client, headers, [{"memberId": "player01", "label": "개근의 여왕"}]) == 1

    assert await _report(client, headers, [
        {"memberId": "nobody", "label": "유령"},
        {"memberId": "player01", "label": ""},
    ]) == 0
    assert len(await _notices(client, headers)) == 1
