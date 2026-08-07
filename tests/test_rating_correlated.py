"""상관을 들고 가는 레이팅 모형이 실제로 그 성질들을 내는지.

지적: "모든 사람이 모든 사람에 대해 균등하게 경기하는 상황이 아니고 특정 사람과만 많이 하고
어떤 사람은 적게나 안 할 수도 있기 때문에, 현재의 모든 데이터를 정합한 예상결과에 대한 점수를
부여하는 거야" / "월 바뀌면서 인위적으로 보정하는 것보다는 사람들 경기수에 비례해서 하는
뭔가 더 깔끔한 방법이 있지 않을까".

여기서 못 박는 것은 손으로 얹은 장치(월초 σ 되돌림, 맞대결 감쇠) 없이도 같은 성질이
모형에서 저절로 나온다는 점이다 — 그 장치들을 다시 넣고 싶어지면 먼저 이 파일을 볼 것.
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


def test_repeat_wins_decay_on_their_own():
    """같은 상대에게 내리 이기면 판마다 값이 떨어진다 — 감쇠 인자 없이, 모형 스스로.

    k번째 판은 그 맞대결에 대한 정보의 1/k만 주기 때문이다. 다섯 번째 승리는 첫 승의
    5분의 1 언저리다."""
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
