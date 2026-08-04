"""활동 목록의 줄 순서와 번호(GET /api/activity/list).

화면이 이 값을 직접 셀 수 없어서 서버가 센다(요청). 목록은 세 곳(도전장·게임결과·
랭크변동)을 시간순으로 섞어 만드는데 어느 한 엔드포인트도 나머지를 모르고, 게임결과는
페이지 단위로 나눠 받으므로 화면은 늘 일부만 쥐고 있다 — 거기서 센 번호는 아직 안
받아온 과거만큼 통째로 어긋난다.

여기서 지키는 것: ① 최신이 위 ② 한 자리에서 이어 친 게임결과는 한 줄 ③ 번호는 아래에서
부터(가장 오래된 줄이 1) ④ 화면에 안 뜨는 스냅샷은 세지 않는다.
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


def _h(tok: dict) -> dict:
    return {"Authorization": f"Bearer {tok['accessToken']}"}


async def _approve(client, admin_token: str, member_id: str) -> None:
    res = await client.patch(
        f"/api/members/{member_id}/status",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "active"},
    )
    assert res.status_code == 200, res.text


async def _register_match(client, headers: dict, day: str) -> dict:
    res = await client.post(
        "/api/game-results",
        headers=headers,
        json={
            "date": day,
            "team1": [{"memberId": "alice", "race": "테란"}],
            "team2": [{"memberId": "bob", "race": "저그"}],
            "result": "team1",
            "matchType": "0101",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


async def test_empty_list(client):
    a = await _signup(client, "alice", "Alice#1001")
    res = await client.get("/api/activity/list", headers=_h(a))
    assert res.status_code == 200, res.text
    # 댓글 칸은 비어 있어도 늘 있다 — 없으면 화면이 매번 있는지부터 확인해야 한다.
    assert res.json() == {"total": 0, "rows": [], "comments": []}


async def test_same_day_games_collapse_into_one_row(client):
    """같은 자리에서 이어 친 경기는 한 줄이다 — 그 줄이 번호 하나를 먹는다(요청)."""
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")
    await _register_match(client, _h(a), "2026-04-01")
    await _register_match(client, _h(a), "2026-04-01")
    last = await _register_match(client, _h(a), "2026-04-01")

    body = (await client.get("/api/activity/list", headers=_h(a))).json()
    assert body["total"] == 1, body
    row = body["rows"][0]
    assert row["kind"] == "gameResultPost"
    assert row["no"] == 1
    # 줄의 열쇠는 묶음의 첫(=가장 최근) 경기다. 시각이 없는 같은 날 경기끼리는 목록을
    # 받는 순서(match_no 내림차순)가 그 첫 자리를 정하는데, 그 순서는 프론트가 목록을
    # 받는 순서(sort=latest)와 같아야 한다 — 여기가 어긋나면 열쇠가 안 맞아 그 줄만
    # 번호를 못 받는다. 셋 중 마지막에 등록된 것이 match_no가 가장 크다.
    assert row["key"] == f"ms-{last['id']}", row


async def test_different_days_are_separate_rows_and_numbered_from_the_bottom(client):
    """날이 다르면 줄이 갈리고, 번호는 가장 오래된 줄이 1이다."""
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")
    await _register_match(client, _h(a), "2026-04-01")
    await _register_match(client, _h(a), "2026-04-02")
    await _register_match(client, _h(a), "2026-04-03")

    body = (await client.get("/api/activity/list", headers=_h(a))).json()
    assert body["total"] == 3, body
    # 최신이 위 → 번호는 위에서부터 3, 2, 1.
    assert [r["no"] for r in body["rows"]] == [3, 2, 1]
    assert all(r["kind"] == "gameResultPost" for r in body["rows"])


async def test_challenge_and_games_share_one_numbering(client):
    """도전장도 같은 번호줄에 낀다 — 종류마다 따로 세지 않는다(요청: 통틀어서 넘버링)."""
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")
    await _register_match(client, _h(a), "2026-04-01")
    res = await client.post(
        "/api/challenges",
        headers=_h(a),
        json={"targetMemberIds": ["bob"], "ownTeamMemberIds": [], "message": "붙자", "scheduledDate": "2026-04-05"},
    )
    assert res.status_code in (200, 201), res.text

    body = (await client.get("/api/activity/list", headers=_h(a))).json()
    assert body["total"] == 2, body
    kinds = [r["no"] for r in body["rows"]]
    assert kinds == [2, 1]
    assert {r["kind"] for r in body["rows"]} == {"challenge", "gameResultPost"}
    # 아직 안 끝난 도전장은 "지금" 위에 서므로 맨 위다.
    assert body["rows"][0]["kind"] == "challenge"


async def test_numbers_are_unique_and_contiguous(client):
    """번호는 1..total을 빠짐없이 한 번씩 쓴다 — 화면에 안 뜨는 것이 번호를 먹으면 안 된다."""
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")
    for day in ("2026-04-01", "2026-04-02", "2026-04-03", "2026-04-04"):
        await _register_match(client, _h(a), day)

    body = (await client.get("/api/activity/list", headers=_h(a))).json()
    nos = sorted(r["no"] for r in body["rows"])
    assert nos == list(range(1, body["total"] + 1))
    assert len({r["key"] for r in body["rows"]}) == body["total"]


async def test_requires_login(client):
    res = await client.get("/api/activity/list")
    assert res.status_code in (401, 403), res.text


async def test_survives_odd_shapes(client, db_session):
    """운영 데이터에서만 나오는 모양들 — 일정 없는 도전장, 기준선만 있는 스냅샷,
    결과 행이 없는 경기. 하나라도 걸리면 목록 전체가 500으로 죽어 번호가 통째로 안 나온다.
    """
    from sqlalchemy import text

    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")

    # 일정 없는 도전장(scheduledDate 없음).
    res = await client.post(
        "/api/challenges",
        headers=_h(a),
        json={"targetMemberIds": ["bob"], "ownTeamMemberIds": [], "message": "언제든"},
    )
    assert res.status_code in (200, 201), res.text

    await _register_match(client, _h(a), "2026-04-01")

    # 변동 없이 기준선만 있는 스냅샷(reason=seed, shifts 빈 배열) — 목록에 안 떠야 한다.
    await db_session.execute(
        text(
            "INSERT INTO ranking_shifts (reason, match_ids, sections, created_at, updated_at)"
            " VALUES ('seed', '[]', :sections, '2026-04-02 00:00:00', '2026-04-02 00:00:00')"
        ),
        {"sections": '[{"matchType": "0101", "standings": [], "shifts": []}]'},
    )
    # 변동이 있는 스냅샷 — 이건 떠야 한다.
    await db_session.execute(
        text(
            "INSERT INTO ranking_shifts (reason, match_ids, sections, created_at, updated_at)"
            " VALUES ('daily', '[]', :sections, '2026-04-03 00:00:00', '2026-04-03 00:00:00')"
        ),
        {"sections": '[{"matchType": "0101", "standings": [], "shifts": [{"memberId": "alice", "nickname": "alice", "from": 2, "to": 1}]}]'},
    )
    await db_session.commit()

    res = await client.get("/api/activity/list", headers=_h(a))
    assert res.status_code == 200, res.text
    body = res.json()
    kinds = sorted(r["kind"] for r in body["rows"])
    # 도전장 1 + 게임결과 1 + 변동 있는 스냅샷 1 = 3줄(기준선만 있는 스냅샷은 빠진다).
    assert kinds == ["challenge", "gameResultPost", "rankingShift"], body
    assert sorted(r["no"] for r in body["rows"]) == [1, 2, 3]


async def test_survives_legacy_snapshot_sections(client, db_session):
    """옛 모양으로 저장된 스냅샷 한 줄이 목록 전체를 죽이면 안 된다.

    sections는 JSON 칸이라 스키마가 강제되지 않는다. 유형마다 한 행이던 시절의 행이 운영
    DB에 그대로 남아 있어 모양이 다른데, dict가 오면 `for sec in sections`가 키(str)를
    돌아 sec.get에서 터진다 — 운영에서 이것 때문에 이 엔드포인트가 계속 500이었고 그래서
    번호가 화면에 아예 안 나왔다. 새 이벤트 목록은 최근 100건만 봐서 옛 행에 안 닿았기에
    멀쩡했다(그래서 원인이 더 안 보였다).

    옛 줄은 화면에 못 그리니 목록에서 빠지고, 멀쩡한 줄들은 그대로 번호를 받아야 한다.
    """
    from sqlalchemy import text

    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")
    await _register_match(client, _h(a), "2026-04-01")

    for sections in (
        '{"0101": {"standings": [], "shifts": []}}',   # 유형별 한 행이던 시절: dict
        '["0101"]',                                    # 칸이 아니라 이름만 담긴 목록
        'null',                                        # 아예 비어 있는 행
    ):
        await db_session.execute(
            text(
                "INSERT INTO ranking_shifts (reason, match_ids, sections, created_at, updated_at)"
                " VALUES ('legacy', '[]', :sections, '2026-03-01 00:00:00', '2026-03-01 00:00:00')"
            ),
            {"sections": sections},
        )
    await db_session.commit()

    res = await client.get("/api/activity/list", headers=_h(a))
    assert res.status_code == 200, res.text
    body = res.json()
    # 옛 줄 셋은 안 뜨고 게임결과 한 줄만 — 번호도 1번 하나다.
    assert body["total"] == 1, body
    assert [r["no"] for r in body["rows"]] == [1], body

    # 이벤트 목록도 같은 줄에 안 걸려야 한다(같은 잣대를 쓴다).
    res = await client.get("/api/activity/ranking-shifts", headers=_h(a))
    assert res.status_code == 200, res.text
    assert res.json() == []


async def test_list_carries_comments_too(client):
    """목록 한 벌은 한 번에 온다(요청: 목록·댓글 단일 API로 통합).

    요청이 둘이면 하나가 늦거나 실패할 때 목록이 반쯤 그려진 채로 남는다 — 실제로 운영에서
    /activity/list와 /activity/comments/all이 나란히 500이었다.
    """
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")
    mid = (await _register_match(client, _h(a), "2026-04-01"))["id"]

    res = await client.post(
        "/api/activity/comments",
        headers=_h(a),
        json={"targetType": "gameResult", "targetId": mid, "text": "좋은 경기"},
    )
    assert res.status_code == 201, res.text

    body = (await client.get("/api/activity/list", headers=_h(a))).json()
    assert body["total"] == 1, body
    assert [c["text"] for c in body["comments"]] == ["좋은 경기"], body
    assert body["comments"][0]["targetType"] == "gameResult"
    assert body["comments"][0]["targetId"] == mid

    # 옛 경로도 아직 같은 값을 준다 — 프론트/API 배포가 어긋나는 순간을 위해 남겨 뒀다.
    old = (await client.get("/api/activity/comments/all", headers=_h(a))).json()
    assert [c["id"] for c in old] == [c["id"] for c in body["comments"]]



async def test_feed_is_one_list_of_items_with_content_and_comments(client):
    """너 나와·랭크 변동·게임결과가 같은 아이템이고, 내용도 댓글도 그 안에 있다(요청).

    화면이 부르는 API는 이것 하나다 — 세 곳을 따로 받아 제 손으로 섞던 것을 서버 한 곳으로
    모았다. 섞는 규칙이 양쪽에 있으면 한쪽만 고쳐지는 순간 번호가 줄과 어긋난다.
    """
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")
    mid = (await _register_match(client, _h(a), "2026-04-01"))["id"]
    res = await client.post(
        "/api/challenges",
        headers=_h(a),
        json={"targetMemberIds": ["bob"], "ownTeamMemberIds": [], "message": "붙자",
              "scheduledDate": "2026-04-05"},
    )
    assert res.status_code in (200, 201), res.text
    res = await client.post(
        "/api/activity/comments",
        headers=_h(a),
        json={"targetType": "gameResult", "targetId": mid, "text": "좋은 경기"},
    )
    assert res.status_code == 201, res.text

    body = (await client.get("/api/activity/feed", headers=_h(a))).json()
    assert body["total"] == 2, body
    assert [i["no"] for i in body["items"]] == [2, 1], body
    assert body["nextCursor"] is None

    challenge, games = body["items"][0], body["items"][1]
    # 도전장 줄에는 도전장 내용이, 게임결과 줄에는 그 자리에서 친 경기들이 담긴다.
    assert challenge["kind"] == "challenge"
    assert challenge["challenge"]["message"] == "붙자"
    assert challenge["gameResults"] == [] and challenge["comments"] == []
    assert games["kind"] == "gameResultPost"
    assert [g["id"] for g in games["gameResults"]] == [mid]
    # 댓글은 그 줄에 달린 것만, 자기 대상을 그대로 들고 온다(카드가 제 것을 찾아 붙는다).
    assert [(c["targetType"], c["targetId"], c["text"]) for c in games["comments"]] \
        == [("gameResult", mid, "좋은 경기")]


async def test_feed_pages_without_gaps_or_repeats(client):
    """페이지를 이어 받아도 번호가 1..total을 빠짐없이 한 번씩 쓴다.

    순서와 번호는 늘 전체를 놓고 세고 자르는 건 그 다음이다 — 페이지 안에서 세면 두 번째
    페이지가 다시 1부터 시작한다.
    """
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")
    for day in ("2026-04-01", "2026-04-02", "2026-04-03", "2026-04-04", "2026-04-05"):
        await _register_match(client, _h(a), day)

    nos: list[int] = []
    keys: list[str] = []
    cursor = None
    for _ in range(10):
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        body = (await client.get("/api/activity/feed", headers=_h(a), params=params)).json()
        nos += [i["no"] for i in body["items"]]
        keys += [i["key"] for i in body["items"]]
        cursor = body["nextCursor"]
        if not cursor:
            break

    assert sorted(nos) == [1, 2, 3, 4, 5], nos
    assert nos == sorted(nos, reverse=True), nos  # 최신이 위
    assert len(set(keys)) == len(keys), keys


async def test_feed_groups_one_session_into_one_item(client):
    """한 자리에서 이어 친 경기는 아이템 하나 — 그 안에 경기들이 다 담긴다."""
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")
    ids = [(await _register_match(client, _h(a), "2026-04-01"))["id"] for _ in range(3)]

    body = (await client.get("/api/activity/feed", headers=_h(a))).json()
    assert body["total"] == 1, body
    item = body["items"][0]
    assert item["kind"] == "gameResultPost"
    assert sorted(g["id"] for g in item["gameResults"]) == sorted(ids), item


async def test_feed_cursor_pointing_at_a_deleted_row_restarts(client):
    """커서가 가리키던 줄이 사라졌으면 처음부터 준다 — 같은 줄을 다시 받는 편이 조용히
    건너뛰어 목록에 구멍이 나는 것보다 낫다."""
    a = await _signup(client, "alice", "Alice#1001")
    body = (await client.get("/api/activity/feed", headers=_h(a), params={"cursor": "ms-99999"})).json()
    assert body["total"] == 0 and body["items"] == []


async def test_feed_requires_login(client):
    res = await client.get("/api/activity/feed")
    assert res.status_code in (401, 403), res.text
