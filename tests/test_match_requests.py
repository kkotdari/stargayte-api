"""대결 요청 코너 — 인박스(언급 알림)만 남았다.

목록/등록/추천/완료 화면이 없어져 그 엔드포인트들을 지웠으므로, 남은 두 경로(인박스 조회와
읽음 처리)만 검증한다. 등록 API가 없어서 알림을 API로 만들 수단이 없다 — 이미 쌓여 있는
알림을 보여주는 게 이 기능의 남은 역할이라, 테스트도 그 상황 그대로 DB에 직접 심어서 만든다.
"""

from sqlalchemy import select

from app.domain.match_requests.models import MatchRequest, MatchRequestTarget
from app.domain.members.models import Member


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


async def _seed_request(db_session, *, text: str, author_id: str, mentioned_ids: list[str]) -> int:
    """언급 알림이 달린 대결 요청 하나를 DB에 직접 심는다(read_at=NULL = 안 읽음)."""
    pk_by_id = {
        m.id: m.pk
        for m in (await db_session.execute(select(Member))).scalars().all()
    }
    request = MatchRequest(text=text, created_by=pk_by_id[author_id], updated_by=pk_by_id[author_id])
    request.targets = [MatchRequestTarget(member_pk=pk_by_id[i]) for i in mentioned_ids]
    request.recommends = []
    db_session.add(request)
    await db_session.commit()
    return request.id


async def test_inbox_shows_unread_mentions_and_marks_them_read(client, db_session):
    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    c = await _signup(client, "carol", "Carol#1003")
    await _approve(client, a["accessToken"], "bob")
    await _approve(client, a["accessToken"], "carol")

    await _seed_request(
        db_session, text="bob 대 carol!", author_id="alice", mentioned_ids=["bob", "carol"],
    )

    # 언급된 bob 인박스에 뜬다 — 함께 언급된 사람도 같이 실린다.
    inbox_b = (await client.get("/api/match-requests/inbox", headers=_h(b))).json()
    assert len(inbox_b["items"]) == 1
    assert inbox_b["items"][0]["text"] == "bob 대 carol!"
    assert inbox_b["items"][0]["author"]["memberId"] == "alice"
    assert {m["memberId"] for m in inbox_b["items"][0]["mentioned"]} == {"bob", "carol"}

    # 언급 안 된 작성자 alice 인박스는 비어있다.
    assert (await client.get("/api/match-requests/inbox", headers=_h(a))).json()["items"] == []

    # bob이 읽음 처리하면 다시 안 뜬다. carol은 여전히 안 읽음이라 그대로 뜬다.
    r = await client.post("/api/match-requests/inbox/read", headers=_h(b))
    assert r.status_code == 200, r.text
    assert (await client.get("/api/match-requests/inbox", headers=_h(b))).json()["items"] == []
    assert len((await client.get("/api/match-requests/inbox", headers=_h(c))).json()["items"]) == 1


async def test_inbox_skips_fulfilled_requests(client, db_session):
    from datetime import UTC, datetime

    a = await _signup(client, "alice", "Alice#1001")
    b = await _signup(client, "bob", "Bob#1002")
    await _approve(client, a["accessToken"], "bob")

    await _seed_request(db_session, text="살아있는 요청", author_id="alice", mentioned_ids=["bob"])
    done_id = await _seed_request(
        db_session, text="이미 성사된 요청", author_id="alice", mentioned_ids=["bob"],
    )
    done = await db_session.get(MatchRequest, done_id)
    done.fulfilled_at = datetime.now(UTC)
    await db_session.commit()

    items = (await client.get("/api/match-requests/inbox", headers=_h(b))).json()["items"]
    assert [it["text"] for it in items] == ["살아있는 요청"]
