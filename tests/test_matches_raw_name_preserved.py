"""리플레이로 등록된 슬롯의 원본 게임 아이디(player_name) 보존 검증.

회원으로 매칭되지 않아 비회원/컴퓨터로 들어가는 슬롯도 리플레이 원본 이름을 그대로
간직해야 한다 — 예전엔 공용 예약값(__unregistered__ 등)으로 덮어써버려서 그 사람이 실제로
누구였는지가 사라졌고, 나중에 유저 매핑 관리 화면에서 회원과 연결할 수조차 없었다.
"""

UNREGISTERED_PREFIX = "__unregistered__"
COMPUTER_PREFIX = "__computer__"


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


async def _create_match(client, headers, team1: list[dict], team2: list[dict]) -> dict:
    res = await client.post(
        "/api/matches",
        headers=headers,
        json={
            "date": "2026-07-01", "team1": team1, "team2": team2,
            "result": "team1", "note": "", "matchType": "0101",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


async def test_unregistered_slot_keeps_its_replay_player_name(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    await _create_match(
        client, headers,
        team1=[{"memberId": "player01", "race": "테란", "playerName": "player01"}],
        team2=[{"memberId": f"{UNREGISTERED_PREFIX}1", "race": "저그", "playerName": "GhostPlayer"}],
    )

    res = await client.get("/api/matches", headers=headers)
    match = res.json()["items"][0]
    # 회원으로 매칭된 슬롯도, 매칭 못 한 슬롯도 리플레이 원본 이름을 그대로 갖고 있어야 한다.
    assert match["team1"][0]["playerName"] == "player01"
    assert match["team2"][0]["playerName"] == "GhostPlayer"
    # 그러면서도 여전히 "실제 회원이 아닌 슬롯"으로 보여야 한다(컴퓨터가 아니라 비회원으로).
    assert match["team2"][0]["memberId"].startswith(UNREGISTERED_PREFIX)


async def test_unregistered_player_name_is_auto_classified(client):
    """비회원으로 들어온 이름도 저장 시 replay_aliases에 kind='unregistered'로 자동 등록된다.

    새 게임아이디는 저장 전에 반드시 회원/컴퓨터/비회원 중 하나로 확정되므로(미분류 저장
    경로 없음), 그 분류를 alias 테이블에 그대로 남겨 모든 게임아이디의 단일 레지스트리로
    유지한다 — 게임아이디 화면에 비회원도 바로 뜨고, 나중에 실제 회원으로 연결하려면
    게임아이디 화면에서 재매핑하면 된다."""
    p1 = await _signup(client, "player01", "Shadow#1001")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    await _create_match(
        client, headers,
        team1=[{"memberId": "player01", "race": "테란", "playerName": "player01"}],
        team2=[{"memberId": f"{UNREGISTERED_PREFIX}1", "race": "저그", "playerName": "GhostPlayer"}],
    )

    res = await client.post(
        "/api/matches/replay-name-classifications/lookup",
        headers=headers,
        json={"rawNames": ["GhostPlayer"]},
    )
    assert res.json()["classifications"] == [{"rawName": "GhostPlayer", "kind": "unregistered"}]

    # 경기결과에서도 컴퓨터가 아니라 비회원으로 보여야 한다.
    match = (await client.get("/api/matches", headers=headers)).json()["items"][0]
    assert match["team2"][0]["memberId"].startswith(UNREGISTERED_PREFIX)


async def test_computer_slot_from_replay_keeps_player_name_and_kind(client):
    p1 = await _signup(client, "player01", "Shadow#1001")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    await _create_match(
        client, headers,
        team1=[{"memberId": "player01", "race": "테란", "playerName": "player01"}],
        team2=[{"memberId": f"{COMPUTER_PREFIX}1", "race": "저그", "playerName": "Computer"}],
    )

    match = (await client.get("/api/matches", headers=headers)).json()["items"][0]
    assert match["team2"][0]["playerName"] == "Computer"
    assert match["team2"][0]["memberId"].startswith(COMPUTER_PREFIX)

    res = await client.post(
        "/api/matches/replay-name-classifications/lookup",
        headers=headers,
        json={"rawNames": ["Computer"]},
    )
    assert res.json()["classifications"] == [{"rawName": "Computer", "kind": "computer"}]


async def test_member_slot_without_player_name_falls_back_to_latest_alias(client):
    """player_name은 절대 비어있을 수 없다 — 회원 슬롯에서 이름이 안 오면(방어적으로) 그
    회원이 등록해둔 가장 최근 게임 아이디로 서버가 대신 채운다. 비회원 슬롯은 리플레이가
    파싱한 실제 이름을 그대로 보존한다."""
    p1 = await _signup(client, "player01", "Shadow#1001")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    await _create_match(
        client, headers,
        team1=[{"memberId": "player01", "race": "테란"}],
        team2=[{"memberId": f"{UNREGISTERED_PREFIX}1", "race": "저그", "playerName": "GhostGuy"}],
    )

    match = (await client.get("/api/matches", headers=headers)).json()["items"][0]
    assert match["team1"][0]["playerName"] == "player01"
    assert match["team2"][0]["playerName"] == "GhostGuy"
    assert match["team2"][0]["memberId"].startswith(UNREGISTERED_PREFIX)


async def test_existing_member_alias_is_not_overwritten(client):
    """이미 어떤 회원의 게임 아이디로 등록된 이름(kind='member')이 비회원 슬롯의
    player_name으로 들어와도, 그 매핑을 덮어쓰거나 유니크 제약에 걸려 터지면 안 된다."""
    p1 = await _signup(client, "player01", "Shadow#1001")
    await _signup(client, "player02", "Mist#1002")
    headers = {"Authorization": f"Bearer {p1['accessToken']}"}

    # "player02"는 signup 때 player02 회원의 replayAlias(kind='member')로 이미 등록돼 있다.
    await _create_match(
        client, headers,
        team1=[{"memberId": "player01", "race": "테란", "playerName": "player01"}],
        team2=[{"memberId": f"{UNREGISTERED_PREFIX}1", "race": "저그", "playerName": "player02"}],
    )

    # 회원 매핑은 그대로 남아야 한다 — 분류 조회(kind != 'member')에는 안 잡힌다.
    res = await client.post(
        "/api/matches/replay-name-classifications/lookup",
        headers=headers,
        json={"rawNames": ["player02"]},
    )
    assert res.json()["classifications"] == []
