"""상관을 들고 가는 레이팅 모형이 실제로 그 성질들을 내는지.

지적: "모든 사람이 모든 사람에 대해 균등하게 경기하는 상황이 아니고 특정 사람과만 많이 하고
어떤 사람은 적게나 안 할 수도 있기 때문에, 현재의 모든 데이터를 정합한 예상결과에 대한 점수를
부여하는 거야" / "월 바뀌면서 인위적으로 보정하는 것보다는 사람들 경기수에 비례해서 하는
뭔가 더 깔끔한 방법이 있지 않을까".

여기서 못 박는 것은 두 가지다. 모형 쪽으로는 월초 σ 되돌림 없이도 σ가 경기수에서 나온다는
것, 점수 쪽으로는 그 점수가 '우리가 너를 얼마나 몰랐나'가 아니라 '누구를 이겼나'를 잰다는 것
(지적: "정구는 타센만 이겼고 미친마법사는 곰세마리 태섭 크리스를 이겼는데 왜 미친마법사가
더 낮지"). 자세한 사정은 service의 POINT_BASE·H2H_REPEAT_DAMP 주석에 있다.
"""
import datetime as dt
from types import SimpleNamespace

from app.domain.game_results.rating import SIGMA0, RatingEngine
from app.domain.game_results.service import _replay_ratings

DAY0 = dt.date(2024, 1, 1)


def _rows(games):
    """games: [(team1_pk, team2_pk, 'team1'|'team2', 날짜오프셋)]"""
    out = []
    for i, (a, b, res, off) in enumerate(games, start=1):
        for team, pk in (("team1", a), ("team2", b)):
            out.append(SimpleNamespace(
                match_id=i, team=team, member_pk=pk, race="랜덤", result=res,
                match_no=f"M{i}", game_started_at=None,
                match_date=DAY0 + dt.timedelta(days=off),
            ))
    return out


def _deltas(games, focal):
    _, d, _ = _replay_ratings(_rows(games), focal=focal)
    return [d[f"M{i}"] for i in range(1, len(games) + 1)]


def _last(games, focal):
    """마지막 경기에서 focal이 얻은 점수 — 그 앞은 판을 깔아 두는 용도다."""
    _, d, _ = _replay_ratings(_rows(games), focal=focal)
    return d[f"M{len(games)}"]


def test_repeat_wins_decay():
    """같은 상대에게 내리 이기면 판마다 값이 절반씩 떨어진다(요청: "4~5판 정도 이겼을
    때부터는 거의 0점")."""
    got = _deltas([(1, 2, "team1", 3 * i) for i in range(8)], focal=1)
    assert all(got[i] > got[i + 1] for i in range(7)), got
    assert got[4] < got[0] * 0.25, got
    assert got[7] < got[0] * 0.15, got


def test_the_loser_stops_losing_too():
    """지는 쪽도 같이 줄어든다 — 늘 지던 상대에게 또 지는 것도 새 정보가 아니다."""
    got = _deltas([(1, 2, "team1", 3 * i) for i in range(8)], focal=2)
    assert all(g < 0 for g in got), got
    assert abs(got[4]) < abs(got[0]) * 0.25, got


def test_a_different_opponent_pays_much_more():
    """같은 상대만 잡은 여덟 판째와, 매번 새 상대를 잡은 여덟 판째의 차이."""
    varied = _deltas([(1, 10 + i, "team1", 3 * i) for i in range(8)], focal=1)
    same = _deltas([(1, 2, "team1", 3 * i) for i in range(8)], focal=1)
    assert same[7] < varied[7] * 0.5, (same, varied)


def test_information_travels_between_people():
    """내가 이긴 적 없는 사람의 값도 움직인다 — 불균등한 대진을 메우는 것이 이 모형의 요점.

    A가 B를 이기고 B가 C를 이기면, A는 C와 한 판도 안 붙었는데도 C보다 위에 있어야 한다."""
    e = RatingEngine()
    e.update([1], [2], "team1")
    e.update([2], [3], "team1")
    assert e.get(1).mu > e.get(2).mu > e.get(3).mu
    # C의 μ는 A와 붙은 적이 없는데도 A의 승리 때문에 이미 움직였다.
    lone = RatingEngine()
    lone.update([2], [3], "team1")
    assert e.get(3).mu != lone.get(3).mu


def test_sigma_falls_with_games_played_and_needs_no_calendar_fix():
    """σ는 경기수에서 나온다 — 달력이 아니라(지적: "사람들 경기수에 비례해서").

    많이 뛴 사람일수록 낮고, 적게 뛴 사람은 높다. 아무리 뛰어도 처음 값(σ0)을 넘지 않는다."""
    e = RatingEngine()
    for i in range(60):
        e.update([1], [2 + i % 4], "team1" if i % 3 else "team2")
    e.update([99], [98], "team1")  # 이제 막 한 판 뛴 사람
    assert e.get(1).sigma < e.get(99).sigma < SIGMA0


def test_the_score_never_moves_the_wrong_way():
    """이긴 사람의 점수가 내려가거나 진 사람의 점수가 올라가는 일은 없다.

    보수레이팅(μ−3σ)을 쓰던 시절에는 σ가 확 줄면서 패자의 점수가 순증가하는 일이 있었다
    (요청: "랭킹 산정시 졌는데 +점수를 받는 이상현상 발생")."""
    games = []
    for i in range(40):
        a, b = 1 + i % 5, 1 + (i * 3 + 1) % 5
        if a != b:
            games.append((a, b, "team1" if i % 4 else "team2", i))
    rows = _rows(games)
    for pk in range(1, 6):
        _, d, _ = _replay_ratings(rows, focal=pk)
        won = {g[3]: (g[0] if g[2] == "team1" else g[1]) for g in games}
        for i, (a, b, res, off) in enumerate(games, start=1):
            if pk not in (a, b):
                continue
            delta = d[f"M{i}"]
            if won[off] == pk:
                assert delta >= 0, (pk, i, delta)
            else:
                assert delta <= 0, (pk, i, delta)


def test_points_do_not_depend_on_how_much_you_have_played():
    """오늘 처음 뛰는 사람이나 백 판 뛴 사람이나, 같은 상대를 이기면 비슷하게 받는다.

    예전에는 점수가 '그 경기로 내 실력 추정치가 얼마나 올랐나'라서, 우리가 잘 모르는
    사람일수록 한 판에 크게 움직였다 — 실측으로 11.6배까지 벌어졌다(지적)."""
    seasoned = [(1, 2 + i % 4, "team1" if i % 3 else "team2", i) for i in range(60)]
    rookie = _last(seasoned + [(1, 90, "team1", 60)], focal=1)      # 1번은 60판을 뛴 사람
    fresh = _last(seasoned + [(80, 90, "team1", 60)], focal=80)     # 80번은 오늘 처음
    assert 0.7 < rookie / fresh < 1.4, (rookie, fresh)


def test_beating_a_stronger_opponent_pays_more():
    """센 상대를 이길수록 많이 받는다 — 점수가 재는 것은 이것 하나다."""
    ladder = []
    for i in range(40):                      # 2번을 세게, 3번을 약하게 만들어 둔다
        ladder.append((2, 4 + i % 5, "team1", i))
        ladder.append((4 + i % 5, 3, "team1", i))
    strong = _last(ladder + [(1, 2, "team1", 40)], focal=1)
    weak = _last(ladder + [(1, 3, "team1", 40)], focal=1)
    assert strong > weak * 1.5, (strong, weak)


def test_a_win_and_the_matching_loss_are_the_same_size():
    """한 판은 ±같은 크기다 — 이긴 쪽이 얻은 만큼 진 쪽이 잃는다."""
    games = [(1, 2, "team1", 0)]
    assert _deltas(games, focal=1)[0] == -_deltas(games, focal=2)[0]


def test_losing_a_match_you_were_expected_to_win_costs_more_than_winning_it_pays():
    """두 판 이긴 상대에게 지면, 같은 자리에서 이겨서 받았을 값보다 크게 깎인다.

    지적: "팍규한테 두 판 이기다가 졌을 때 너무 적게 깎이는 거 아니야? 내 생각엔 이길
    확률이 높으니까 많이 깎일 것 같은데". 한 판의 값이 '이길 확률'로 정해지므로, 이길
    것 같던 판을 지면 그만큼 크게 잃는 것이 맞다."""
    lead = [(1, 2, "team1", 0), (1, 2, "team1", 1)]
    if_won = _last(lead + [(1, 2, "team1", 2)], focal=1)
    if_lost = _last(lead + [(2, 1, "team1", 2)], focal=1)
    assert if_won > 0 > if_lost
    assert abs(if_lost) > if_won * 1.4, (if_won, if_lost)
