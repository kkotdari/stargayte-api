"""달이 바뀌어도 지난달까지의 상대강도를 들고 시작하는가(요청: "월별로 제로베이스에서
시작하는 게 오히려 객관적이지 않다 — 월이 바뀌어도 지난달까지의 누적치를 가지고 상대강도를
계산해서 평가를 시작해야 한다").

예전에는 조회 기간 이전 경기를 SQL에서 통째로 잘라내(rank_replay_rows의 date_from) 매달
전원이 기본 레이팅에서 다시 시작했다. 이제 재생은 늘 맨 처음부터 하고, 조회 기간은 '점수를
세기 시작하는 날'로만 쓴다. 그 차이가 실제로 순위를 가르는 최소 픽스처로 확인한다.
"""

from tests.test_game_results_ranking import _match, _signup_many


async def _stats(client, headers, *, date_from: str, date_to: str) -> dict:
    res = await client.get(
        "/api/game-results/stats",
        headers=headers,
        params={"matchType": "0101", "dateFrom": date_from, "dateTo": date_to},
    )
    assert res.status_code == 200, res.text
    return {m["memberId"]: m for m in res.json()["members"]}


async def test_last_month_decides_who_is_the_strong_opponent(client):
    """이번 달 성적이 같아도, 지난달에 강해진 쪽을 이긴 사람이 더 높은 점수를 받는다.

    지난달에 p3가 p5·p6을 이겨 강자가 되고 p4는 아무것도 안 한다. 이번 달에는 p1이 p3를,
    p2가 p4를 이긴다 — 둘 다 1승 0패다. 이월이 되면 p1의 1승이 더 값나가고, 이월이 없으면
    (예전 방식) p3와 p4가 이번 달 시작 시점에 똑같은 신규라 둘의 점수가 같아진다.
    """
    headers = await _signup_many(client, 6)
    # 지난달 — p3를 강자로 키운다.
    await _match(client, headers, ["player03"], ["player05"], "team1", "2026-01-10")
    await _match(client, headers, ["player03"], ["player06"], "team1", "2026-01-11")
    # 이번 달 — 둘 다 1승이지만 상대의 무게가 다르다.
    await _match(client, headers, ["player01"], ["player03"], "team1", "2026-02-10")
    await _match(client, headers, ["player02"], ["player04"], "team1", "2026-02-10")

    feb = await _stats(client, headers, date_from="2026-02-01", date_to="2026-02-28")
    assert feb["player01"]["rankScore"] > feb["player02"]["rankScore"]


async def test_score_counts_only_the_window_but_rating_carries(client):
    """점수는 조회한 달에 번 것만 센다 — 지난달 승수가 이번 달 점수에 얹히지 않는다.

    p3는 1월에 두 판을 이기고 2월엔 한 판도 안 뛴다. 2월 조회에서 p3는 '이 기간 0경기'라
    점수가 빈칸이어야 한다(이월된 것은 실력 추정이지 점수가 아니다). 1월 조회에서는 제 점수가
    그대로 있다.
    """
    headers = await _signup_many(client, 6)
    await _match(client, headers, ["player03"], ["player05"], "team1", "2026-01-10")
    await _match(client, headers, ["player03"], ["player06"], "team1", "2026-01-11")
    await _match(client, headers, ["player01"], ["player02"], "team1", "2026-02-10")

    jan = await _stats(client, headers, date_from="2026-01-01", date_to="2026-01-31")
    assert jan["player03"]["rankScore"] is not None and jan["player03"]["rankScore"] > 0

    feb = await _stats(client, headers, date_from="2026-02-01", date_to="2026-02-28")
    assert feb["player03"]["rankScore"] is None


async def test_later_matches_do_not_leak_into_an_earlier_month(client):
    """뒤에 친 경기는 앞선 달 조회에 안 섞인다 — date_to는 그대로 잘라낸다.

    1월만 봤을 때의 값은, 2월 경기가 등록되기 전이든 후든 똑같아야 한다.
    """
    headers = await _signup_many(client, 6)
    await _match(client, headers, ["player01"], ["player02"], "team1", "2026-01-10")
    before = await _stats(client, headers, date_from="2026-01-01", date_to="2026-01-31")

    await _match(client, headers, ["player01"], ["player03"], "team1", "2026-02-10")
    after = await _stats(client, headers, date_from="2026-01-01", date_to="2026-01-31")

    assert before["player01"]["rankScore"] == after["player01"]["rankScore"]


async def test_the_last_month_matches_the_all_time_view(client):
    """마지막 달까지 본 점수 = 올타임 점수.

    점수는 '그 기간에 번 값'이 아니라 '그 시점까지의 기록으로 본 실력'이다(요청: "이번 달에
    번 점수는 필요없어"). 그러니 마지막 경기가 든 달까지 보면 전체를 본 것과 같아야 한다.
    달마다의 값은 그때그때의 실력이라 더하는 값이 아니다.
    """
    headers = await _signup_many(client, 6)
    await _match(client, headers, ["player01"], ["player02"], "team1", "2026-01-10")
    await _match(client, headers, ["player03"], ["player01"], "team1", "2026-01-20")
    await _match(client, headers, ["player01"], ["player03"], "team1", "2026-02-10")
    await _match(client, headers, ["player02"], ["player01"], "team1", "2026-03-05")

    mar = await _stats(client, headers, date_from="2026-03-01", date_to="2026-03-31")
    res = await client.get("/api/game-results/stats", headers=headers, params={"matchType": "0101"})
    assert res.status_code == 200, res.text
    total = {m["memberId"]: m for m in res.json()["members"]}["player01"]["rankScore"]
    assert abs(total - mar["player01"]["rankScore"]) < 0.15


async def test_sigma_drift_keeps_monthly_scores_comparable(client):
    """시간이 지나면 σ가 조금 되돌아온다 — 그래야 달마다의 점수 폭이 계속 쪼그라들지 않는다.

    같은 사람이 매달 똑같이 한 판씩 이기는데, 되돌리기가 없으면 σ가 단조 감소해 세 번째 달의
    점수가 첫 달의 몇 분의 일로 주저앉는다(실측으로 1월 230점 → 12월 10점까지 갔다).
    되돌리기가 있으면 그렇게까지 벌어지지 않는다.
    """
    headers = await _signup_many(client, 8)
    for i, month in enumerate(("01", "02", "03"), start=0):
        for day, foe in (("05", 3), ("15", 4), ("25", 5)):
            await _match(
                client, headers, ["player01"], [f"player{foe + i:02d}"], "team1",
                f"2026-{month}-{day}",
            )

    scores = []
    for month, last in (("01", "31"), ("02", "28"), ("03", "31")):
        s = await _stats(client, headers, date_from=f"2026-{month}-01", date_to=f"2026-{month}-{last}")
        scores.append(s["player01"]["rankScore"])

    assert all(v > 0 for v in scores)
    # 세 번째 달이 첫 달의 1/5 아래로 내려가면 되돌리기가 안 먹은 것이다.
    assert scores[2] > scores[0] / 5
