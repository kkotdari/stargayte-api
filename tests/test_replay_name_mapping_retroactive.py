"""게임아이디 화면에서 나중에 회원으로 매핑하면, 이미 등록된 경기의 "비회원" 참가자
자리도 소급으로 그 회원을 가리키게 되는지 확인한다(matches.repository의
resolve_placeholder_raw_name_to_member)."""


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


async def test_unregistered_slot_resolves_retroactively_when_mapped_to_member(client):
    # 첫 번째 가입자는 자동으로 운영자+active가 된다(MemberService 규칙).
    admin = await _signup(client, "admin1", "Admin#1001")
    headers = {"Authorization": f"Bearer {admin['accessToken']}"}
    await _signup(client, "ghost1", "Ghost#2002")

    # 경기 등록 시 team2 자리를 "비회원"(player_name="GhostRawName")으로 채운다.
    res = await client.post(
        "/api/matches",
        headers=headers,
        json={
            "date": "2026-01-01",
            "team1": [{"memberId": "admin1", "race": "테란"}],
            "team2": [{"memberId": "__unregistered__0", "race": "프로토스", "playerName": "GhostRawName"}],
            "status": "completed",
            "result": "team1",
            "matchType": "0101",
        },
    )
    assert res.status_code == 200, res.text
    match_id = res.json()["id"]
    assert res.json()["team2"][0]["memberId"] == "__unregistered__0"
    assert res.json()["team2"][0]["playerName"] == "GhostRawName"

    # 게임아이디 화면에서 "GhostRawName"을 실제 회원(ghost1)으로 연결한다.
    res = await client.post(
        "/api/matches/replay-name-mappings",
        headers=headers,
        json={"rawName": "GhostRawName", "kind": "member", "memberId": "ghost1"},
    )
    assert res.status_code == 200, res.text

    # 같은 경기를 다시 조회 — team2 슬롯이 이제 ghost1 회원을 가리켜야 한다.
    res = await client.get("/api/matches", headers=headers, params={"limit": 10})
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    match = next(m for m in items if m["id"] == match_id)
    assert match["team2"][0]["memberId"] == "ghost1", (
        f"expected retroactive resolve to ghost1, got {match['team2'][0]}"
    )
    # player_name은 지우지 않고 보존한다 — 회원의 battletag는 나중에 바뀔 수 있어, 이
    # 경기 시점에 실제로 어떤 인게임 아이디였는지 기록해두는 유일한 값이다.
    assert match["team2"][0]["playerName"] == "GhostRawName"
