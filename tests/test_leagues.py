"""리그(League/Tournament) 도메인 테스트 — CRUD, 로스터 중복/개인리그 제약, 빈 대진표
생성 후 시드 배정(부전승 정확성 — 특히 부전승 팀이 다음 라운드에서 실제 상대와 붙어야
하는 경우), 시드 오버라이드, 결과가 난 경기의 보호 규칙, 비운영자 403.

경기 결과를 입력·취소하는 엔드포인트는 쓰는 화면이 없어 지웠다. 그래서 "이미 결과가 난
경기"를 API로 만들 수단이 없다 — 그 상태를 전제로 하는 보호 규칙(팀 삭제·로스터 변경·시드
변경·대진표 규모 변경 차단)은 여전히 살아있는 코드라, 결과 행을 DB에 직접 심어 검증한다
(_decide_match)."""

import math
import string

from sqlalchemy import select

from app.domain.leagues.models import League, LeagueMatch, LeagueTeam


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


async def _approve(client, admin_token: str, member_id: str) -> None:
    res = await client.patch(
        f"/api/members/{member_id}/status",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "active"},
    )
    assert res.status_code == 200, res.text


async def _bootstrap(client, n: int) -> tuple[dict, list[dict]]:
    """admin(첫 가입자, 자동 운영자+active) 헤더와 n명의 승인된 일반 회원 헤더 목록."""
    admin = await _signup(client, "admin", "Admin#0001")
    admin_headers = {"Authorization": f"Bearer {admin['accessToken']}"}
    members = []
    for i in range(n):
        mid = f"m{i}"
        m = await _signup(client, mid, f"M{i}#100{i}")
        await _approve(client, admin["accessToken"], mid)
        members.append({"Authorization": f"Bearer {m['accessToken']}"})
    return admin_headers, members


async def _create_league(client, headers, *, name="리그", mode="team", best_of=3) -> dict:
    res = await client.post(
        "/api/leagues", headers=headers,
        json={"name": name, "mode": mode, "bestOf": best_of},
    )
    assert res.status_code == 200, res.text
    return res.json()


# 팀 추가/로스터 변경/팀 삭제와 슬롯 배정은 개별 엔드포인트가 없어졌다 — 화면이 쓰는 일괄
# 저장 두 개(PUT /teams, PUT /bracket/seeding)로만 한다. 아래 헬퍼들이 "지금 상태를 읽어
# 한 군데만 바꿔 통째로 다시 보내는" 그 변환을 대신해, 각 테스트 본문은 예전 의미 그대로 둔다.


async def _get_league(client, headers, league_id: int) -> dict:
    res = await client.get(f"/api/leagues/{league_id}", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _composition_of(league: dict) -> list[dict]:
    """현재 팀 구성을 일괄 저장 payload 모양으로."""
    return [
        {"id": t["id"], "roster": [m["memberId"] for m in t["roster"]]}
        for t in league["teams"]
    ]


async def _save_composition(client, headers, league_id: int, teams: list[dict]):
    return await client.put(
        f"/api/leagues/{league_id}/teams", headers=headers, json={"teams": teams},
    )


async def _add_teams(client, headers, league_id: int, n: int) -> list[dict]:
    """빈 팀 n개를 덧붙이고, 새로 생긴 팀들만 라벨 순서로 돌려준다.

    응답의 teams는 라벨 문자열 순(A, AA, AB, B, ...)이라 그대로 쓰면 순서가 어긋난다 —
    라벨은 스프레드시트 열 이름 방식이므로 (길이, 문자열)로 정렬해야 A..Z, AA, AB가 된다."""
    league = await _get_league(client, headers, league_id)
    before = {t["id"] for t in league["teams"]}
    res = await _save_composition(
        client, headers, league_id, _composition_of(league) + [{"id": None, "roster": []}] * n,
    )
    assert res.status_code == 200, res.text
    fresh = [t for t in res.json()["teams"] if t["id"] not in before]
    return sorted(fresh, key=lambda t: (len(t["label"]), t["label"]))


async def _add_team(client, headers, league_id: int) -> dict:
    return (await _add_teams(client, headers, league_id, 1))[0]


async def _set_roster(client, headers, league_id: int, team_id: int, member_ids: list[str]):
    """한 팀의 로스터만 바꿔 통째로 다시 저장한다."""
    league = await _get_league(client, headers, league_id)
    teams = _composition_of(league)
    for entry in teams:
        if entry["id"] == team_id:
            entry["roster"] = member_ids
            break
    else:
        raise AssertionError(f"팀 {team_id}이(가) 리그에 없다")
    return await _save_composition(client, headers, league_id, teams)


async def _delete_team(client, headers, league_id: int, team_id: int):
    """그 팀만 빼고 통째로 다시 저장한다(빠진 팀은 삭제된다)."""
    league = await _get_league(client, headers, league_id)
    teams = [e for e in _composition_of(league) if e["id"] != team_id]
    return await _save_composition(client, headers, league_id, teams)


async def _generate_bracket(client, headers, league_id: int, team_count: int):
    """이 팀 수가 들어갈 만한 판을 만든다.

    엔드포인트는 이제 '몇 라운드짜리 판인가'를 받는다(요청: 규모를 직접 정하기) — 어느 칸에나
    팀을 앉힐 수 있게 되면서 팀 수로 판 크기를 정하는 계산이 성립하지 않게 됐다. 테스트는
    여전히 "N팀짜리 상황"을 말하고 싶으므로, 여기서 N을 담을 최소 라운드로 바꿔 준다."""
    rounds = max(1, math.ceil(math.log2(max(2, team_count))))
    return await client.post(
        f"/api/leagues/{league_id}/bracket/generate", headers=headers, json={"rounds": rounds},
    )


def _round1_assignments(league: dict) -> list[dict]:
    """편집 가능한 1라운드 자리의 현재 배정 — 일괄 시드 저장 payload 모양으로.

    서버가 보는 '편집 가능'과 같은 조건이다: 1라운드 & 부전 자리 아님 & 실제 결과 없음.
    빠진 자리는 비우는 것으로 간주되므로 반드시 전체를 실어야 한다."""
    out = []
    for m in league["matches"]:
        if m["round"] != 1 or m["isDead"] or m["setsWonA"] is not None:
            continue
        for side in ("a", "b"):
            team = m["teamA"] if side == "a" else m["teamB"]
            out.append({"matchId": m["id"], "side": side, "teamId": team["id"] if team else None})
    return out


async def _save_seeding(client, headers, league_id: int, assignments: list[dict]):
    return await client.put(
        f"/api/leagues/{league_id}/bracket/seeding",
        headers=headers, json={"assignments": assignments},
    )


async def _assign_slot(client, headers, league_id: int, match_id: int, side: str, team_id: int | None):
    """한 자리만 바꿔 1라운드 시드를 통째로 다시 저장한다.

    한 팀은 1라운드에 한 번만 올 수 있어서, 옮겨 갈 팀이 다른 자리에 이미 있으면 그 자리를
    비운다 — 없어진 자리별 배정 API(set_match_slot)가 하던 동작 그대로다."""
    league = await _get_league(client, headers, league_id)
    assignments = _round1_assignments(league)
    target = next(
        (a for a in assignments if a["matchId"] == match_id and a["side"] == side), None
    )
    if target is None:
        # 편집 불가 자리 — 서버가 거부해야 하는 경우라 그대로 한 건만 보내 확인한다.
        return await _save_seeding(
            client, headers, league_id, [{"matchId": match_id, "side": side, "teamId": team_id}],
        )
    if team_id is not None:
        for a in assignments:
            if a["teamId"] == team_id:
                a["teamId"] = None
    target["teamId"] = team_id
    return await _save_seeding(client, headers, league_id, assignments)


async def _confirm_bracket(client, headers, league_id: int):
    return await client.post(f"/api/leagues/{league_id}/bracket/confirm", headers=headers)


def _match(league: dict, round_: int, slot: int) -> dict:
    m = next(m for m in league["matches"] if m["round"] == round_ and m["slotInRound"] == slot)
    return m


async def _decide_match(db_session, match_id: int, *, sets_a: int, sets_b: int) -> None:
    """경기 하나에 '실제 결과가 났음'을 DB에 직접 새긴다 — 결과 입력 엔드포인트가 없어서다.

    보호 규칙들이 보는 건 sets_won_a가 채워졌는지와 승자가 정해졌는지뿐이라, 다음 라운드
    전파까지는 흉내내지 않는다(전파는 부전승 경로로 따로 검증된다)."""
    match = await db_session.get(LeagueMatch, match_id)
    match.sets_won_a = sets_a
    match.sets_won_b = sets_b
    match.winner_team_id = match.team_a_id if sets_a > sets_b else match.team_b_id
    await db_session.commit()


async def test_non_admin_forbidden(client):
    admin_headers, members = await _bootstrap(client, 1)
    res = await client.get("/api/leagues", headers=members[0])
    assert res.status_code == 403, res.text
    res = await client.post("/api/leagues", headers=members[0], json={"name": "x", "mode": "team"})
    assert res.status_code == 403, res.text


async def test_create_get_delete_league(client):
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, name="가을리그", best_of=3)
    assert league["status"] == "setup"
    assert league["mode"] == "team"
    assert league["drawSize"] is None

    res = await client.get(f"/api/leagues/{league['id']}", headers=admin_headers)
    assert res.status_code == 200
    assert res.json()["name"] == "가을리그"

    res = await client.delete(f"/api/leagues/{league['id']}", headers=admin_headers)
    assert res.status_code == 204
    res = await client.get(f"/api/leagues/{league['id']}", headers=admin_headers)
    assert res.status_code == 404


async def test_team_creation_labels_unlimited_and_multichar_after_26(client):
    """팀/선수 수는 상한이 없다(요청: "팀수 무제한 개인전 선수 무제한 대진표 슬롯
    무제한"). 26개(Z)를 넘어가면 라벨이 스프레드시트 열 이름 방식(AA, AB..)으로
    이어진다."""
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, mode="team")
    teams = await _add_teams(client, admin_headers, league["id"], 28)
    assert [t["label"] for t in teams[:26]] == list(string.ascii_uppercase)
    assert teams[26]["label"] == "AA"
    assert teams[27]["label"] == "AB"


async def test_individual_league_allows_more_than_24(client):
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, mode="individual")
    teams = await _add_teams(client, admin_headers, league["id"], 30)
    assert len(teams) == 30
    assert teams[29]["label"] == "AD"


async def test_team_delete_relabels_remaining(client):
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers)
    teams = await _add_teams(client, admin_headers, league["id"], 3)  # A, B, C
    res = await _delete_team(client, admin_headers, league["id"], teams[0]["id"])
    assert res.status_code == 200, res.text
    body = res.json()
    assert [t["label"] for t in body["teams"]] == ["A", "B"]


async def test_roster_rejects_cross_team_duplicate_and_bad_count(client):
    admin_headers, members = await _bootstrap(client, 3)
    league = await _create_league(client, admin_headers)
    teams = await _add_teams(client, admin_headers, league["id"], 2)

    res = await _set_roster(client, admin_headers, league["id"], teams[0]["id"], ["m0", "m1"])
    assert res.status_code == 200, res.text

    # m0은 이미 팀A 소속 — 팀B에 다시 넣으면 409.
    res = await _set_roster(client, admin_headers, league["id"], teams[1]["id"], ["m0", "m2"])
    assert res.status_code == 409, res.text

    # 같은 팀 안에서 같은 회원 두 번 — 스키마 검증(FastAPI 기본 422)에서 걸린다.
    res = await _set_roster(client, admin_headers, league["id"], teams[1]["id"], ["m2", "m2"])
    assert res.status_code == 422, res.text


async def test_individual_league_roster_locked_to_one(client):
    """개인리그는 한 자리에 선수 1명뿐이다.

    대타 불가 규칙도 같이 걸려 있었지만 대타는 결과 입력 payload로만 지정할 수 있어서,
    그 엔드포인트를 지우면서 규칙 자체가 함께 사라졌다."""
    admin_headers, members = await _bootstrap(client, 2)
    league = await _create_league(client, admin_headers, mode="individual", best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 2)

    res = await _set_roster(client, admin_headers, league["id"], teams[0]["id"], ["m0", "m1"])
    assert res.status_code == 400, res.text  # 개인리그는 1명만

    res = await _set_roster(client, admin_headers, league["id"], teams[0]["id"], ["m0"])
    assert res.status_code == 200, res.text
    res = await _set_roster(client, admin_headers, league["id"], teams[1]["id"], ["m1"])
    assert res.status_code == 200, res.text


async def test_bracket_generate_rounds_validation(client):
    """판 크기는 라운드 수로 직접 받는다(요청) — 1라운드(2칸)부터 열 라운드까지."""
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers)
    await _add_teams(client, admin_headers, league["id"], 3)

    async def gen(rounds):
        return await client.post(
            f"/api/leagues/{league['id']}/bracket/generate",
            headers=admin_headers, json={"rounds": rounds},
        )

    assert (await gen(0)).status_code == 422
    assert (await gen(11)).status_code == 422
    # 팀 수보다 작은 판도 만들 수 있다 — 어느 칸에나 앉힐 수 있게 되면서 "팀 수 = 판 크기"가
    # 아니게 됐고, 안 쓰는 가지는 확정할 때 사라진다. 담을 자리보다 많은 팀은 팀구성 저장이
    # 따로 막는다(test_add_team_capped_at_bracket_capacity).
    res = await gen(1)
    assert res.status_code == 200, res.text
    assert res.json()["drawSize"] == 2
    res = await gen(5)
    assert res.status_code == 200, res.text
    assert res.json()["drawSize"] == 32


async def test_generate_bracket_can_be_resized_before_results_but_not_after(client, db_session):
    """팀수/대진표 슬롯 수는 대진표 생성 후에도 다시 잡을 수 있다(요청: "팀수, 대진표
    슬롯 수 다 수정가능해야돼") — 단, 실제 경기 결과가 하나라도 들어갔으면 재생성이
    그 진행 상황을 지워버리므로 막는다."""
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 2)

    res = await _generate_bracket(client, admin_headers, league["id"], 2)
    assert res.status_code == 200, res.text
    assert res.json()["drawSize"] == 2

    res = await _generate_bracket(client, admin_headers, league["id"], 4)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["drawSize"] == 4
    assert body["plannedTeams"] == 4

    slot0 = _match(body, 1, 0)
    await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[0]["id"])
    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "b", teams[1]["id"])
    assert res.status_code == 200, res.text
    await _decide_match(db_session, slot0["id"], sets_a=1, sets_b=0)

    res = await _generate_bracket(client, admin_headers, league["id"], 8)
    assert res.status_code == 400, res.text


async def test_generate_bracket_resize_preserves_round1_assignments(client):
    """참가팀수를 늘려 규모를 다시 잡아도 이미 1라운드에 배정해둔 팀은 그대로 남는다
    (요청: "참가팀수 늘릴때 기존 지정된건 리셋하지 말아줘") — 결과가 없으니 2라운드
    이상은 구조가 바뀌는 김에 새로 만들어져도 손실이 없다."""
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 4)  # A, B, C, D

    res = await _generate_bracket(client, admin_headers, league["id"], 4)
    assert res.status_code == 200, res.text
    body = res.json()
    slot0 = _match(body, 1, 0)
    await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[0]["id"])
    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "b", teams[1]["id"])
    assert res.status_code == 200, res.text

    # 4 -> 6팀으로 늘리면 draw_size가 4에서 8로 커진다 — 슬롯0(A vs B)은 그대로 남고,
    # 새로 생긴 슬롯(2,3)만 빈 채로 추가된다.
    res = await _generate_bracket(client, admin_headers, league["id"], 6)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["drawSize"] == 8
    slot0 = _match(body, 1, 0)
    assert slot0["teamA"]["label"] == "A" and slot0["teamB"]["label"] == "B"
    assert _match(body, 1, 2)["teamA"] is None and _match(body, 1, 3)["teamA"] is None


async def test_bracket_generates_empty_and_slots_are_assigned_manually(client):
    """대진표는 팀이 있건 없건, 있어도 자동으로 채워 넣지 않고 항상 빈 채로 생성된다 —
    각 칸에 누가 들어갈지는 슬롯 API로 직접 정한다(요청: "대진표 생성 누르면 빈 대진표가
    생기고 각 칸에 누가 들어갈지 정할 수 있는 시스템으로")."""
    admin_headers, members = await _bootstrap(client, 2)
    league = await _create_league(client, admin_headers, best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 2)
    for t, mid in zip(teams, ["m0", "m1"]):
        assert (await _set_roster(client, admin_headers, league["id"], t["id"], [mid])).status_code == 200

    res = await _generate_bracket(client, admin_headers, league["id"], 4)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["drawSize"] == 4
    assert body["plannedTeams"] == 4
    # 팀이 이미 2개 있었어도 자동으로 안 채워지고 전부 비어 있어야 한다.
    for m in body["matches"]:
        assert m["teamA"] is None and m["teamB"] is None
        if m["round"] == 1:
            assert not m["isDead"]  # 2팀뿐이지만 4자리를 예약했으니 전부 아직 살아있음

    slot0 = _match(body, 1, 0)
    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[0]["id"])
    assert res.status_code == 200, res.text
    assert _match(res.json(), 1, 0)["teamA"]["label"] == "A"

    # 죽은(is_dead) 슬롯에는 배정할 수 없다 — 슬롯1(리프 2,3)은 team_count=4 안이라
    # 살아있고, 4강 나머지는 이 테스트에서 안 다루지만 최소 확인: 이미 결과가 난 경기엔
    # 배정 불가(다른 테스트에서 커버) / 존재하지 않는 매치 404 등은 기존 커버리지로 충분.


async def test_add_team_capped_at_bracket_capacity(client):
    """대진표가 있으면 그 판이 담을 수 있는 인원(draw_size)을 넘는 팀은 둘 수 없다."""
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers)
    await _add_teams(client, admin_headers, league["id"], 2)
    res = await _generate_bracket(client, admin_headers, league["id"], 3)  # 2라운드 → 4칸
    assert res.status_code == 200, res.text
    assert res.json()["drawSize"] == 4

    league_now = await _get_league(client, admin_headers, league["id"])
    base = _composition_of(league_now)

    res = await _save_composition(
        client, admin_headers, league["id"], base + [{"id": None, "roster": []}] * 2,
    )
    assert res.status_code == 200, res.text  # 4팀 — 판에 자리가 있다

    res = await _save_composition(
        client, admin_headers, league["id"], base + [{"id": None, "roster": []}] * 3,
    )
    assert res.status_code == 400, res.text  # 5팀 — 4칸짜리 판을 넘는다


async def test_three_team_bye_resolves_on_confirm_and_still_plays_next_round(client):
    """3팀 — C를 1라운드 슬롯0에 혼자 앉히면 확정 순간 부전승으로 올라간다.

    부전승은 이제 따로 찍는 자리가 아니라 '한 사람만 있는 가지'의 결과다(요청). 그리고
    결승에서는 A-vs-B 승자와 실제로 붙어야 한다 — 부전승만으로 우승이 되면 안 된다
    (한 번 고쳤던 버그라 그대로 지킨다)."""
    admin_headers, members = await _bootstrap(client, 3)
    league = await _create_league(client, admin_headers, best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 3)  # A, B, C
    for t, mid in zip(teams, ["m0", "m1", "m2"]):
        assert (await _set_roster(client, admin_headers, league["id"], t["id"], [mid])).status_code == 200

    res = await _generate_bracket(client, admin_headers, league["id"], 3)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["drawSize"] == 4
    m0, m1 = _match(body, 1, 0), _match(body, 1, 1)

    res = await _seed(client, admin_headers, league["id"], [
        {"matchId": m0["id"], "side": "a", "teamId": teams[2]["id"]},
        {"matchId": m1["id"], "side": "a", "teamId": teams[0]["id"]},
        {"matchId": m1["id"], "side": "b", "teamId": teams[1]["id"]},
    ])
    assert res.status_code == 200, res.text
    # 확정 전에는 아무것도 자동으로 올라가지 않는다 — 어느 가지가 살지 아직 안 정해졌다.
    assert _match(res.json(), 1, 0)["winnerTeamId"] is None

    res = await _confirm_bracket(client, admin_headers, league["id"])
    assert res.status_code == 200, res.text
    body = res.json()
    assert _match(body, 1, 0)["winnerTeamId"] == teams[2]["id"]  # C 부전승
    final = _match(body, 2, 0)
    assert final["winnerTeamId"] is None      # 결승은 아직 안 끝났다
    assert final["teamA"]["label"] == "C"
    assert final["teamB"] is None             # A-vs-B 결과를 기다리는 중


async def test_six_team_two_byes_meet_in_a_real_round2(client):
    """6팀 — A·B를 1라운드에 혼자 앉히면 둘 다 부전승으로 올라가고, 2라운드에서 서로
    실제로 붙는다(자동 진출로 결승까지 새어 나가면 안 된다)."""
    admin_headers, members = await _bootstrap(client, 6)
    league = await _create_league(client, admin_headers, best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 6)  # A..F
    for t, mid in zip(teams, [f"m{i}" for i in range(6)]):
        assert (await _set_roster(client, admin_headers, league["id"], t["id"], [mid])).status_code == 200

    res = await _generate_bracket(client, admin_headers, league["id"], 6)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["drawSize"] == 8
    ms = {i: _match(body, 1, i) for i in range(4)}

    res = await _seed(client, admin_headers, league["id"], [
        {"matchId": ms[0]["id"], "side": "a", "teamId": teams[0]["id"]},   # A 혼자
        {"matchId": ms[1]["id"], "side": "a", "teamId": teams[1]["id"]},   # B 혼자
        {"matchId": ms[2]["id"], "side": "a", "teamId": teams[2]["id"]},
        {"matchId": ms[2]["id"], "side": "b", "teamId": teams[3]["id"]},
        {"matchId": ms[3]["id"], "side": "a", "teamId": teams[4]["id"]},
        {"matchId": ms[3]["id"], "side": "b", "teamId": teams[5]["id"]},
    ])
    assert res.status_code == 200, res.text
    res = await _confirm_bracket(client, admin_headers, league["id"])
    assert res.status_code == 200, res.text
    body = res.json()

    assert _match(body, 1, 0)["winnerTeamId"] == teams[0]["id"]
    assert _match(body, 1, 1)["winnerTeamId"] == teams[1]["id"]
    r2_0 = _match(body, 2, 0)
    assert {r2_0["teamA"]["label"], r2_0["teamB"]["label"]} == {"A", "B"}
    assert r2_0["winnerTeamId"] is None        # 진짜 경기다
    assert _match(body, 3, 0)["winnerTeamId"] is None


async def test_slot_reassign_moves_team_and_blocks_after_decided(client, db_session):
    admin_headers, members = await _bootstrap(client, 4)
    league = await _create_league(client, admin_headers, best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 4)
    for t, mid in zip(teams, [f"m{i}" for i in range(4)]):
        assert (await _set_roster(client, admin_headers, league["id"], t["id"], [mid])).status_code == 200
    res = await _generate_bracket(client, admin_headers, league["id"], 4)
    body = res.json()
    slot0, slot1 = _match(body, 1, 0), _match(body, 1, 1)

    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[0]["id"])
    assert res.status_code == 200, res.text

    # 이미 슬롯0에 배정된 팀(teams[0]=A)을 슬롯1에 다시 배정하면 슬롯0에서 자동으로
    # 빠지고 슬롯1로 옮겨간다(요청: "이미 지정된 팀도 드롭다운에 나오고 새로 지정하면
    # 기존 지정된 슬롯을 미지정으로 지우는 식").
    res = await _assign_slot(client, admin_headers, league["id"], slot1["id"], "a", teams[0]["id"])
    assert res.status_code == 200, res.text
    body = res.json()
    assert _match(body, 1, 0)["teamA"] is None
    assert _match(body, 1, 1)["teamA"]["label"] == "A"

    # 슬롯 비우기는 허용.
    res = await _assign_slot(client, admin_headers, league["id"], slot1["id"], "a", None)
    assert res.status_code == 200, res.text
    assert _match(res.json(), 1, 1)["teamA"] is None

    # 이미 결과가 난 자리는 시드 변경 대상에서 아예 빠진다 — 그 자리로 배정을 보내면 거부된다.
    await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[0]["id"])
    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "b", teams[1]["id"])
    assert res.status_code == 200, res.text
    await _decide_match(db_session, slot0["id"], sets_a=1, sets_b=0)

    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[2]["id"])
    assert res.status_code == 400, res.text


async def test_nothing_advances_before_confirm(client):
    """확정 전에는 아무것도 자동으로 올라가지 않는다 — 대진 모양은 확정 순간에 굳는다(요청).

    예전에는 '부전승 자리'에 팀이 앉는 순간 바로 진출 처리돼서, 시드를 고치는 동안에도
    다음 라운드가 채워졌다 지워졌다 했다. 이제 그 판단은 확정 한 곳에서만 한다."""
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 3)  # A, B, C
    res = await _generate_bracket(client, admin_headers, league["id"], 3)
    m0 = _match(res.json(), 1, 0)

    res = await _assign_slot(client, admin_headers, league["id"], m0["id"], "a", teams[0]["id"])
    assert res.status_code == 200, res.text
    body = res.json()
    assert _match(body, 1, 0)["teamA"]["label"] == "A"
    assert _match(body, 1, 0)["winnerTeamId"] is None   # 아직 부전승이 아니다
    assert _match(body, 2, 0)["teamA"] is None          # 결승도 비어 있다

    # 다른 팀으로 바꿔 앉히는 것도 그대로 된다(확정 전이라 잠긴 것이 없다).
    res = await _assign_slot(client, admin_headers, league["id"], m0["id"], "a", teams[2]["id"])
    assert res.status_code == 200, res.text
    assert _match(res.json(), 1, 0)["teamA"]["label"] == "C"


async def test_confirm_bracket_locks_seed_changes(client):
    """대진 확정 버튼을 누르면 그 뒤로는 시드(슬롯) 변경 자체가 막힌다(요청: "대진
    확정 버튼을 추가해주고 그걸 누르면 그때부터 시드는 변경 못하게")."""
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, best_of=1)
    teams = await _add_teams(client, admin_headers, league["id"], 2)
    res = await _generate_bracket(client, admin_headers, league["id"], 2)
    body = res.json()
    assert body["bracketLocked"] is False
    slot0 = _match(body, 1, 0)

    # 아무도 안 앉은 판은 확정할 수 없다 — 확정은 대진 모양을 굳히는 일이라, 굳힐 것이
    # 없으면 판이 통째로 죽는다.
    res = await _confirm_bracket(client, admin_headers, league["id"])
    assert res.status_code == 400, res.text
    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[1]["id"])
    assert res.status_code == 200, res.text

    res = await _confirm_bracket(client, admin_headers, league["id"])
    assert res.status_code == 200, res.text
    assert res.json()["bracketLocked"] is True

    res = await _assign_slot(client, admin_headers, league["id"], slot0["id"], "a", teams[0]["id"])
    assert res.status_code == 409, res.text

    res = await _generate_bracket(client, admin_headers, league["id"], 4)
    assert res.status_code == 409, res.text  # 규모 변경도 확정 후엔 막힌다


async def test_delete_league_cascades(client, db_session):
    admin_headers, members = await _bootstrap(client, 2)
    league = await _create_league(client, admin_headers)
    teams = await _add_teams(client, admin_headers, league["id"], 2)
    await _set_roster(client, admin_headers, league["id"], teams[0]["id"], ["m0"])

    res = await client.delete(f"/api/leagues/{league['id']}", headers=admin_headers)
    assert res.status_code == 204

    remaining_leagues = (await db_session.execute(select(League))).scalars().all()
    remaining_teams = (await db_session.execute(select(LeagueTeam))).scalars().all()
    assert remaining_leagues == []
    assert remaining_teams == []  # 로스터가 있던 팀도 리그 삭제로 같이 지워져야 한다


async def _seed(client, headers, league_id: int, assignments: list[dict]):
    return await client.put(
        f"/api/leagues/{league_id}/bracket/seeding",
        headers=headers, json={"assignments": assignments},
    )


async def test_bracket_seeding_batch_atomic_swap(client):
    """1라운드 시드를 한 번에 저장하고, 두 팀을 맞바꾸는 편집도 원자적으로 반영되는지 —
    자리별 순차 저장이면 '팀 이동' 자동비움이 이미 넣은 자리를 덮어써 깨지던 케이스."""
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, mode="team")
    teams = await _add_teams(client, admin_headers, league["id"], 4)  # A, B, C, D
    res = await _generate_bracket(client, admin_headers, league["id"], 4)
    assert res.status_code == 200, res.text
    lg = res.json()
    m0, m1 = _match(lg, 1, 0), _match(lg, 1, 1)

    # 최초 시드: m0=(A,B), m1=(C,D)
    res = await _seed(client, admin_headers, league["id"], [
        {"matchId": m0["id"], "side": "a", "teamId": teams[0]["id"]},
        {"matchId": m0["id"], "side": "b", "teamId": teams[1]["id"]},
        {"matchId": m1["id"], "side": "a", "teamId": teams[2]["id"]},
        {"matchId": m1["id"], "side": "b", "teamId": teams[3]["id"]},
    ])
    assert res.status_code == 200, res.text
    lg = res.json()
    assert _match(lg, 1, 0)["teamA"]["id"] == teams[0]["id"]
    assert _match(lg, 1, 0)["teamB"]["id"] == teams[1]["id"]
    assert _match(lg, 1, 1)["teamA"]["id"] == teams[2]["id"]
    assert _match(lg, 1, 1)["teamB"]["id"] == teams[3]["id"]

    # A <-> C 스왑: m0.a=C, m1.a=A (나머지 그대로). 최종 상태가 정확히 반영돼야 한다.
    res = await _seed(client, admin_headers, league["id"], [
        {"matchId": m0["id"], "side": "a", "teamId": teams[2]["id"]},
        {"matchId": m0["id"], "side": "b", "teamId": teams[1]["id"]},
        {"matchId": m1["id"], "side": "a", "teamId": teams[0]["id"]},
        {"matchId": m1["id"], "side": "b", "teamId": teams[3]["id"]},
    ])
    assert res.status_code == 200, res.text
    lg = res.json()
    assert _match(lg, 1, 0)["teamA"]["id"] == teams[2]["id"]
    assert _match(lg, 1, 0)["teamB"]["id"] == teams[1]["id"]
    assert _match(lg, 1, 1)["teamA"]["id"] == teams[0]["id"]
    assert _match(lg, 1, 1)["teamB"]["id"] == teams[3]["id"]


async def test_bracket_seeding_batch_then_confirm_resolves_bye(client):
    """일괄 저장은 배정만 하고, 부전승은 확정에서 한 번에 정리된다."""
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, mode="team")
    teams = await _add_teams(client, admin_headers, league["id"], 3)  # A, B, C
    res = await _generate_bracket(client, admin_headers, league["id"], 3)
    assert res.status_code == 200, res.text
    lg = res.json()
    m0, m1 = _match(lg, 1, 0), _match(lg, 1, 1)
    res = await _seed(client, admin_headers, league["id"], [
        {"matchId": m0["id"], "side": "a", "teamId": teams[0]["id"]},
        {"matchId": m1["id"], "side": "a", "teamId": teams[1]["id"]},
        {"matchId": m1["id"], "side": "b", "teamId": teams[2]["id"]},
    ])
    assert res.status_code == 200, res.text
    assert _match(res.json(), 1, 0)["winnerTeamId"] is None   # 저장만으로는 안 올라간다

    res = await _confirm_bracket(client, admin_headers, league["id"])
    assert res.status_code == 200, res.text
    lg = res.json()
    assert _match(lg, 1, 0)["winnerTeamId"] == teams[0]["id"]  # A 부전승
    assert _match(lg, 2, 0)["teamA"]["id"] == teams[0]["id"]


async def test_bracket_seeding_rejects_duplicate_team_and_locked(client):
    admin_headers, _ = await _bootstrap(client, 0)
    league = await _create_league(client, admin_headers, mode="team")
    teams = await _add_teams(client, admin_headers, league["id"], 4)
    res = await _generate_bracket(client, admin_headers, league["id"], 4)
    lg = res.json()
    m0, m1 = _match(lg, 1, 0), _match(lg, 1, 1)

    # 같은 팀을 두 자리에 → 거부.
    res = await _seed(client, admin_headers, league["id"], [
        {"matchId": m0["id"], "side": "a", "teamId": teams[0]["id"]},
        {"matchId": m1["id"], "side": "a", "teamId": teams[0]["id"]},
    ])
    assert res.status_code == 400, res.text

    # 정상 저장 후 대진 확정 → 그 뒤 일괄 저장은 잠겨서 거부.
    res = await _seed(client, admin_headers, league["id"], [
        {"matchId": m0["id"], "side": "a", "teamId": teams[0]["id"]},
        {"matchId": m0["id"], "side": "b", "teamId": teams[1]["id"]},
        {"matchId": m1["id"], "side": "a", "teamId": teams[2]["id"]},
        {"matchId": m1["id"], "side": "b", "teamId": teams[3]["id"]},
    ])
    assert res.status_code == 200, res.text
    await _confirm_bracket(client, admin_headers, league["id"])
    res = await _seed(client, admin_headers, league["id"], [
        {"matchId": m0["id"], "side": "a", "teamId": teams[1]["id"]},
    ])
    assert res.status_code == 409, res.text


async def _set_composition(client, headers, league_id: int, teams: list[dict]):
    return await client.put(
        f"/api/leagues/{league_id}/teams", headers=headers, json={"teams": teams},
    )


async def test_team_composition_batch_create_and_move_member(client):
    """새 팀 생성+로스터 지정을 한 번에, 그리고 멤버를 팀 사이로 옮기는 편집이 (league_id,
    member_pk) 유니크 충돌 없이 원자적으로 반영되는지."""
    admin_headers, _ = await _bootstrap(client, 3)  # m0, m1, m2
    league = await _create_league(client, admin_headers, mode="team")
    res = await _set_composition(client, admin_headers, league["id"], [
        {"id": None, "roster": ["m0", "m1"]},
        {"id": None, "roster": ["m2"]},
    ])
    assert res.status_code == 200, res.text
    lg = res.json()
    assert [t["label"] for t in lg["teams"]] == ["A", "B"]
    a, b = lg["teams"][0], lg["teams"][1]
    assert [r["memberId"] for r in a["roster"]] == ["m0", "m1"]
    assert [r["memberId"] for r in b["roster"]] == ["m2"]

    # m1을 A -> B로 이동. 기존 팀 id는 유지.
    res = await _set_composition(client, admin_headers, league["id"], [
        {"id": a["id"], "roster": ["m0"]},
        {"id": b["id"], "roster": ["m2", "m1"]},
    ])
    assert res.status_code == 200, res.text
    lg = res.json()
    ta = next(t for t in lg["teams"] if t["id"] == a["id"])
    tb = next(t for t in lg["teams"] if t["id"] == b["id"])
    assert [r["memberId"] for r in ta["roster"]] == ["m0"]
    assert [r["memberId"] for r in tb["roster"]] == ["m2", "m1"]


async def test_team_composition_batch_delete_relabels(client):
    admin_headers, _ = await _bootstrap(client, 3)
    league = await _create_league(client, admin_headers, mode="team")
    res = await _set_composition(client, admin_headers, league["id"], [
        {"id": None, "roster": ["m0"]},
        {"id": None, "roster": ["m1"]},
        {"id": None, "roster": ["m2"]},
    ])
    lg = res.json()
    a, _b, c = lg["teams"]  # A, B, C
    # B 삭제(payload에서 뺌) → 남은 A, C가 A, B로 재라벨.
    res = await _set_composition(client, admin_headers, league["id"], [
        {"id": a["id"], "roster": ["m0"]},
        {"id": c["id"], "roster": ["m2"]},
    ])
    assert res.status_code == 200, res.text
    lg = res.json()
    assert [t["label"] for t in lg["teams"]] == ["A", "B"]
    assert lg["teams"][0]["id"] == a["id"]
    assert lg["teams"][1]["id"] == c["id"]  # C였던 팀이 살아남아 B 라벨


async def test_team_composition_rejects_member_in_two_teams(client):
    admin_headers, _ = await _bootstrap(client, 1)
    league = await _create_league(client, admin_headers, mode="team")
    res = await _set_composition(client, admin_headers, league["id"], [
        {"id": None, "roster": ["m0"]},
        {"id": None, "roster": ["m0"]},
    ])
    assert res.status_code == 409, res.text


async def test_team_composition_individual_rejects_multi(client):
    admin_headers, _ = await _bootstrap(client, 2)
    league = await _create_league(client, admin_headers, mode="individual")
    res = await _set_composition(client, admin_headers, league["id"], [
        {"id": None, "roster": ["m0", "m1"]},
    ])
    assert res.status_code == 400, res.text


async def test_team_composition_protects_decided_team(client, db_session):
    admin_headers, _ = await _bootstrap(client, 3)
    league = await _create_league(client, admin_headers, mode="team", best_of=1)
    res = await _set_composition(client, admin_headers, league["id"], [
        {"id": None, "roster": ["m0"]},
        {"id": None, "roster": ["m1"]},
    ])
    lg = res.json()
    a, b = lg["teams"]
    res = await _generate_bracket(client, admin_headers, league["id"], 2)
    m0 = _match(res.json(), 1, 0)
    await _seed(client, admin_headers, league["id"], [
        {"matchId": m0["id"], "side": "a", "teamId": a["id"]},
        {"matchId": m0["id"], "side": "b", "teamId": b["id"]},
    ])
    await _decide_match(db_session, m0["id"], sets_a=1, sets_b=0)

    # A는 결과가 난 팀 — 삭제 시도 거부.
    res = await _set_composition(client, admin_headers, league["id"], [
        {"id": b["id"], "roster": ["m1"]},
    ])
    assert res.status_code == 400, res.text
    # 로스터 변경 시도도 거부.
    res = await _set_composition(client, admin_headers, league["id"], [
        {"id": a["id"], "roster": ["m0", "m2"]},
        {"id": b["id"], "roster": ["m1"]},
    ])
    assert res.status_code == 400, res.text


async def test_team_composition_respects_planned_teams(client):
    admin_headers, _ = await _bootstrap(client, 4)
    league = await _create_league(client, admin_headers, mode="team")
    res = await _set_composition(client, admin_headers, league["id"], [
        {"id": None, "roster": ["m0"]},
        {"id": None, "roster": ["m1"]},
    ])
    lg = res.json()
    a, b = lg["teams"]
    # 2팀짜리 대진표 생성 → planned_teams=2.
    res = await _generate_bracket(client, admin_headers, league["id"], 2)
    assert res.status_code == 200, res.text
    # 예약(2)을 넘는 3팀 시도 → 거부.
    res = await _set_composition(client, admin_headers, league["id"], [
        {"id": a["id"], "roster": ["m0"]},
        {"id": b["id"], "roster": ["m1"]},
        {"id": None, "roster": ["m2"]},
    ])
    assert res.status_code == 400, res.text
