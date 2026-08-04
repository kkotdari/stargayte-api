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
    assert res.json() == {"total": 0, "rows": []}


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
