"""활동 댓글 — 대상(target_type, target_id)이 경기든 너 나와!든 같은 테이블/API 하나로
달리고, 작성자 본인/운영자만 수정·삭제한다.
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


def _h(tok: dict) -> dict:
    return {"Authorization": f"Bearer {tok['accessToken']}"}


async def _approve(client, admin_token: str, member_id: str) -> None:
    res = await client.patch(
        f"/api/members/{member_id}/status",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "active"},
    )
    assert res.status_code == 200, res.text


async def _register_match(client, headers: dict) -> dict:
    res = await client.post(
        "/api/game-results",
        headers=headers,
        json={
            "date": "2026-04-01",
            "team1": [{"memberId": "alice", "race": "테란"}],
            "team2": [{"memberId": "bob", "race": "저그"}],
            "result": "team1",
            "matchType": "0101",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


async def test_activity_comment_crud_on_match(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")
    match = await _register_match(client, _h(a))
    mid = match["id"]

    # 작성(언급 포함).
    res = await client.post(
        "/api/activity/comments",
        headers=_h(a),
        json={"targetType": "gameResult", "targetId": mid, "text": "@bob 좋은 경기!", "targetMemberIds": ["bob"]},
    )
    assert res.status_code == 201, res.text
    comment = res.json()
    assert comment["targetType"] == "gameResult"
    assert comment["targetId"] == mid
    assert comment["author"]["memberId"] == "alice"
    assert comment["mentions"][0]["memberId"] == "bob"

    # 대상별 조회.
    res = await client.get(
        "/api/activity/comments",
        headers=_h(b),
        params={"targetType": "gameResult", "targetId": mid},
    )
    assert res.status_code == 200, res.text
    items = res.json()
    assert len(items) == 1
    # 남의 댓글은 일반 회원이 수정할 수 없다.
    assert items[0]["canEdit"] is False

    # 작성자 아닌 회원의 수정은 거부된다.
    cid = comment["id"]
    res = await client.patch(
        f"/api/activity/comments/{cid}", headers=_h(b), json={"text": "고쳐쓰기"},
    )
    assert res.status_code == 403

    # 작성자 본인 수정.
    res = await client.patch(
        f"/api/activity/comments/{cid}", headers=_h(a), json={"text": "정정합니다"},
    )
    assert res.status_code == 200
    assert res.json()["text"] == "정정합니다"

    # 작성자 본인 삭제.
    res = await client.delete(f"/api/activity/comments/{cid}", headers=_h(a))
    assert res.status_code == 204

    res = await client.get(
        "/api/activity/comments",
        headers=_h(a),
        params={"targetType": "gameResult", "targetId": mid},
    )
    assert res.json() == []


async def test_activity_comment_edit_keeps_same_mention(client):
    """언급을 그대로 유지한 채 수정해도 UNIQUE 제약 충돌로 500이 나면 안 된다(버그 회귀).

    예전엔 한 flush에서 멘션을 통째로 재할당해 같은 (comment_id, member_pk)를
    지우기 전에 다시 INSERT하다 UNIQUE 제약에 걸렸다.
    """
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    c = await _signup(client, "choi", "Choi#1003")
    await _approve(client, a["accessToken"], "bob")
    await _approve(client, a["accessToken"], "choi")
    match = await _register_match(client, _h(a))
    mid = match["id"]

    res = await client.post(
        "/api/activity/comments",
        headers=_h(a),
        json={"targetType": "gameResult", "targetId": mid, "text": "@bob 좋은 경기!", "targetMemberIds": ["bob"]},
    )
    assert res.status_code == 201, res.text
    cid = res.json()["id"]

    # 같은 언급 유지 — 예전 버그 재현 지점.
    res = await client.patch(
        f"/api/activity/comments/{cid}",
        headers=_h(a),
        json={"text": "@bob 수정했어요", "targetMemberIds": ["bob"]},
    )
    assert res.status_code == 200, res.text
    assert [m["memberId"] for m in res.json()["mentions"]] == ["bob"]

    # 언급 제거.
    res = await client.patch(
        f"/api/activity/comments/{cid}",
        headers=_h(a),
        json={"text": "언급 없앰", "targetMemberIds": []},
    )
    assert res.status_code == 200, res.text
    assert res.json()["mentions"] == []

    # 다른 유저로 언급 교체.
    res = await client.patch(
        f"/api/activity/comments/{cid}",
        headers=_h(a),
        json={"text": "@choi 로 바꿈", "targetMemberIds": ["choi"]},
    )
    assert res.status_code == 200, res.text
    assert [m["memberId"] for m in res.json()["mentions"]] == ["choi"]


async def test_activity_comment_on_challenge_target(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")

    # 너 나와! 하나 생성해 그 id를 대상으로 댓글을 단다.
    res = await client.post(
        "/api/challenges",
        headers=_h(a),
        json={"targetMemberIds": ["bob"], "matchType": "0101", "message": ""},
    )
    assert res.status_code in (200, 201), res.text
    challenge_id = res.json()["id"]

    res = await client.post(
        "/api/activity/comments",
        headers=_h(b),
        json={"targetType": "challenge", "targetId": challenge_id, "text": "기대되는 매치"},
    )
    assert res.status_code == 201, res.text

    res = await client.get(
        "/api/activity/comments",
        headers=_h(a),
        params={"targetType": "challenge", "targetId": challenge_id},
    )
    assert res.status_code == 200
    assert [c["text"] for c in res.json()] == ["기대되는 매치"]


async def _register_match_today(client, headers: dict, *, result: str = "team1") -> dict:
    """스냅샷은 '이번 달(KST)' 성적으로 계산되므로 오늘 날짜로 등록한다."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    res = await client.post(
        "/api/game-results",
        headers=headers,
        json={
            "date": today,
            "team1": [{"memberId": "alice", "race": "테란"}],
            "team2": [{"memberId": "bob", "race": "저그"}],
            "result": result,
            "matchType": "0101",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


async def _recompute(client) -> None:
    """매일 자정 스케줄러가 하는 일 그대로 — 순위표를 다시 집계해 변동을 남긴다."""
    from app.db.session import AsyncSessionLocal
    from app.domain.activity.service import RankingShiftService
    from app.main import _rank_entries_computer

    async with AsyncSessionLocal() as session:
        await RankingShiftService(session).recompute_daily(await _rank_entries_computer(session))


async def test_register_no_longer_creates_snapshot(client):
    """경기 등록만으로는 변동 카드가 생기지 않는다(요청).

    예전엔 등록/삭제마다 계산해서 하루에도 여러 번 피드에 떴다. 이제 재집계는 매일
    자정 스케줄러 한 곳에서만 한다."""
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")

    m1 = await _register_match_today(client, _h(a))
    assert m1["id"]  # 등록 자체는 정상
    res = await client.get("/api/activity/ranking-shifts", headers=_h(a))
    assert res.status_code == 200, res.text
    assert res.json() == []


async def test_daily_recompute_first_run_is_silent_baseline(client):
    """기준선이 없으면 첫 재집계는 변동 없이 기준선으로만 남는다.

    비교 대상이 없는데 변동을 내면 순위표에 있는 전원이 '신규 진입'으로 쏟아진다."""
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")

    await _register_match_today(client, _h(a))
    await _recompute(client)
    res = await client.get("/api/activity/ranking-shifts", headers=_h(a))
    assert res.json() == []  # 기준선만 깔린다

    # 그다음 경기부터는 이 기준선과 비교돼 변동이 잡힌다 — 아무도 '신규'가 아니다.
    await _register_match_today(client, _h(a), result="team2")
    await _register_match_today(client, _h(a), result="team2")
    await _recompute(client)
    res = await client.get("/api/activity/ranking-shifts", headers=_h(a))
    events = res.json()
    assert len(events) >= 1
    assert events[0]["reason"] == "daily"
    assert all(
        s["from"] is not None
        for ev in events for sec in ev["sections"] for s in sec["shifts"]
    )


async def test_shift_carries_point_change(client):
    """순위 변동에 포인트 변동도 함께 실린다(요청: 순위 변동 옆에 "+100p").

    몇 계단 올랐는지만으로는 그게 한 판 차이인지 몰아친 결과인지 알 수가 없다."""
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")

    await _register_match_today(client, _h(a))
    await _recompute(client)  # 기준선
    await _register_match_today(client, _h(a), result="team2")
    await _register_match_today(client, _h(a), result="team2")
    await _recompute(client)

    events = (await client.get("/api/activity/ranking-shifts", headers=_h(a))).json()
    shifts = [s for ev in events for sec in ev["sections"] for s in sec["shifts"]]
    assert shifts
    assert all(s["fromPoints"] is not None and s["toPoints"] is not None for s in shifts)
    # 순위가 바뀔 만큼 경기를 했으면 포인트도 실제로 움직였어야 한다.
    assert any(s["toPoints"] != s["fromPoints"] for s in shifts)


async def test_daily_recompute_is_idempotent(client):
    """순위표가 그대로면 다시 돌려도 아무것도 안 남는다 — 매일 도는 작업이라 중요하다."""
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")
    await _register_match_today(client, _h(a))
    await _recompute(client)
    await _register_match_today(client, _h(a), result="team2")
    await _recompute(client)
    before = (await client.get("/api/activity/ranking-shifts", headers=_h(a))).json()

    await _recompute(client)  # 경기 변화 없이 한 번 더
    after = (await client.get("/api/activity/ranking-shifts", headers=_h(a))).json()
    assert [e["id"] for e in after] == [e["id"] for e in before]


async def test_seed_lays_one_row_with_both_types(client, db_session):
    """기준선은 하루 한 행이고, 그 안에 개인전·팀전 칸이 함께 들어간다(요청).

    예전엔 유형마다 행이 따로라 "개인전 행만 있고 팀전 행이 없는" 어중간한 상태를 따로
    살펴야 했다 — 이제 한 행이 두 칸을 다 갖는다."""
    from sqlalchemy import select

    from app.domain.activity.models import RankingShift
    from app.main import _seed_ranking_shifts

    await _signup(client, "alice", "Alice#1001")
    await _seed_ranking_shifts()
    db_session.expire_all()
    rows = (await db_session.scalars(select(RankingShift))).all()
    assert len(rows) == 1
    assert [sec["matchType"] for sec in rows[0].sections] == ["0101", "0102"]

    # 멱등 — 다시 부팅해도 행이 늘지 않는다.
    await _seed_ranking_shifts()
    db_session.expire_all()
    assert len((await db_session.scalars(select(RankingShift))).all()) == 1


async def test_rank_snapshot_new_month_starts_fresh_baseline(client, db_session):
    """달이 바뀌면 지난달 순위표와 비교하지 않는다(요청).

    순위표가 '이번 달' 성적만으로 매겨지므로, 지난달 표를 기준 삼으면 이번 달에 처음
    뛴 사람이 전부 '신규 진입'이 된다(매달 1일마다). 그 첫 재집계는 조용한 기준선으로만
    남고, 그다음부터 정상적으로 변동이 잡힌다."""
    from datetime import timedelta

    from sqlalchemy import select

    from app.domain.activity.models import RankingShift
    from app.main import _seed_ranking_shifts

    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")
    await _seed_ranking_shifts()

    # 기준선을 '지난달'로 밀어 둔다.
    for row in (await db_session.scalars(select(RankingShift))).all():
        row.created_at = row.created_at - timedelta(days=40)
    await db_session.commit()

    await _register_match_today(client, _h(a))
    await _recompute(client)
    res = await client.get("/api/activity/ranking-shifts", headers=_h(a))
    assert res.json() == []  # 월초 '전원 신규' 카드가 뜨지 않는다

    await _register_match_today(client, _h(a), result="team2")
    await _register_match_today(client, _h(a), result="team2")
    await _recompute(client)
    events = (await client.get("/api/activity/ranking-shifts", headers=_h(a))).json()
    assert len(events) >= 1
    assert all(
        s["from"] is not None
        for ev in events for sec in ev["sections"] for s in sec["shifts"]
    )


async def test_activity_comment_on_rankshift_target(client):
    """순위변동 알림 카드에도 같은 댓글 API가 그대로 붙는다(요청)."""
    a = await _signup(client, "alice", "Alice#1001")
    await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")

    # 기준선 한 번, 그다음 변동 한 번 — 그래야 댓글을 달 변동 카드가 생긴다.
    await _register_match_today(client, _h(a))
    await _recompute(client)
    await _register_match_today(client, _h(a), result="team2")
    await _register_match_today(client, _h(a), result="team2")
    await _recompute(client)
    res = await client.get("/api/activity/ranking-shifts", headers=_h(a))
    assert res.status_code == 200, res.text
    snap_id = res.json()[0]["id"]

    res = await client.post(
        "/api/activity/comments",
        headers=_h(a),
        json={
            "targetType": "rankingShift", "targetId": snap_id,
            "text": "@Bob#1002 축하!", "targetMemberIds": ["bob"],
        },
    )
    assert res.status_code == 201, res.text
    created = res.json()
    assert created["targetType"] == "rankingShift"

    res = await client.get(
        f"/api/activity/comments?targetType=rankingShift&targetId={snap_id}", headers=_h(a)
    )
    assert res.status_code == 200, res.text
    listed = res.json()
    assert [c["id"] for c in listed] == [created["id"]]
    assert listed[0]["mentions"][0]["memberId"] == "bob"

    # 이름을 통일하기 전 값(rankshift)으로 물어봐도 같은 댓글이 나와야 한다 — 프론트와
    # 백엔드 배포가 어긋나는 순간에 옛 프론트가 옛 값을 보낼 수 있다.
    res = await client.get(
        f"/api/activity/comments?targetType=rankshift&targetId={snap_id}", headers=_h(a)
    )
    assert res.status_code == 200, res.text
    assert [c["id"] for c in res.json()] == [created["id"]]


async def test_legacy_target_type_is_normalized(client):
    """옛 이름(match)으로 달아도 새 이름(gameResult)으로 저장되고 그렇게 읽힌다."""
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")
    match = await _register_match_today(client, _h(a))
    mid = match["id"]

    res = await client.post(
        "/api/activity/comments",
        headers=_h(a),
        json={"targetType": "match", "targetId": mid, "text": "옛 이름으로 달린 댓글"},
    )
    assert res.status_code == 201, res.text
    assert res.json()["targetType"] == "gameResult"

    res = await client.get(
        "/api/activity/comments", headers=_h(b),
        params={"targetType": "gameResult", "targetId": mid},
    )
    assert res.status_code == 200, res.text
    assert [c["text"] for c in res.json()] == ["옛 이름으로 달린 댓글"]


async def test_challenge_delete_admin_only(client):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")

    res = await client.post(
        "/api/challenges",
        headers=_h(a),
        json={"targetMemberIds": ["bob"], "matchType": "0101", "message": ""},
    )
    cid = res.json()["id"]

    # 일반 회원은 삭제 불가.
    res = await client.delete(f"/api/challenges/{cid}", headers=_h(b))
    assert res.status_code == 403

    # 운영자(첫 가입자)는 삭제 가능 — 달린 피드 댓글도 함께 사라진다.
    await client.post(
        "/api/activity/comments",
        headers=_h(b),
        json={"targetType": "challenge", "targetId": cid, "text": "곧 사라질 댓글"},
    )
    res = await client.delete(f"/api/challenges/{cid}", headers=_h(a))
    assert res.status_code == 204
    res = await client.get(
        "/api/activity/comments", headers=_h(a),
        params={"targetType": "challenge", "targetId": cid},
    )
    assert res.json() == []


async def test_legacy_feed_prefix_still_answers(client) -> None:
    """옛 경로(/api/feed/...)도 그대로 받는다 — 프론트와 서버는 따로 배포되므로 새 서버가
    먼저 뜨는 동안 아직 옛 프론트가 이 주소를 부른다(api/router.py 주석 참고). 이 별칭을
    지우려면 이 테스트부터 지워야 한다는 표시이기도 하다."""
    tok = await _signup(client, "legacy1", "레거시#1")
    res = await client.get("/api/feed/comments/all", headers=_h(tok))
    assert res.status_code == 200, res.text


async def test_legacy_target_type_rows_are_still_found(client, db_session):
    """옛 이름(match/rankshift)으로 저장된 댓글도 새 이름으로 조회된다(지적: 기존 댓글 연결 안 됨).

    저장된 값을 새 이름으로 옮기는 부팅 단계가 따로 있지만, 그게 아직 안 돈 DB나 배포가
    어긋난 순간에 들어온 댓글에는 옛 이름이 남는다. 조회를 새 이름 하나로만 걸면 그런
    댓글이 통째로 안 보인다 — 대상별 조회에서 실제로 빠졌다.
    """
    from sqlalchemy import text

    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")
    match = await _register_match(client, _h(a))
    mid = match["id"]

    # 새 이름으로 한 건 남긴다.
    res = await client.post(
        "/api/activity/comments",
        headers=_h(a),
        json={"targetType": "gameResult", "targetId": mid, "text": "새 이름"},
    )
    assert res.status_code == 201, res.text

    # 옛 이름으로 저장된 한 건을 직접 심는다(마이그레이션 전 상태 재현).
    await db_session.execute(
        text(
            "INSERT INTO feed_comments (target_type, target_id, text, created_by, updated_by,"
            " created_at, updated_at)"
            " SELECT 'match', :mid, '옛 이름', pk, pk, created_at, created_at FROM members WHERE id = 'alice'"
        ),
        {"mid": mid},
    )
    await db_session.commit()

    res = await client.get(
        "/api/activity/comments",
        headers=_h(a),
        params={"targetType": "gameResult", "targetId": mid},
    )
    assert res.status_code == 200, res.text
    items = res.json()
    texts = sorted(c["text"] for c in items)
    assert texts == ["새 이름", "옛 이름"], items
    # 내보낼 때는 둘 다 새 이름으로 통일된다.
    assert {c["targetType"] for c in items} == {"gameResult"}
