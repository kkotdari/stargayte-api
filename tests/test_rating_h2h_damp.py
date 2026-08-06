"""맞대결 반복 감쇠 — 같은 상대와 자꾸 붙으면 그 판의 점수가 줄어든다.

요청: "같은 사람에게 이겼을때/졌을때 얻고 잃는 점수가 너무 적게 줄어드는거 같아. 사실
4~5판 정도 이겼을 때부터는 거의 0점이어야 한다고 생각되는데".

여기서 못 박는 것은 '연승'이 아니라 '맞붙은 횟수'로 센다는 점이다 — 연승으로 세면 결과가
뒤집힐 때마다 값이 되살아나, 꾸준히 이기는 사람만 깎이고 들쭉날쭉한 사람이 이득을 본다
(실측으로 클럽 랭킹이 통째로 뒤집혔다). 자세한 사정은 service.H2H_REPEAT_DAMP 주석에 있다.
"""
import datetime as dt
from types import SimpleNamespace

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


def test_repeat_wins_decay_to_near_nothing():
    """사흘 간격으로 여섯 판을 내리 이기면, 다섯 번째부터는 첫 판의 10%도 안 된다."""
    got = _deltas([(1, 2, "team1", 3 * i) for i in range(6)], focal=1)
    assert all(got[i] > got[i + 1] for i in range(5)), got
    assert got[4] < got[0] * 0.1, got


def test_the_loser_stops_losing_too():
    """지는 쪽도 같은 비율로 깎인다(요청: "이겼을때/졌을때") — 늘 지던 상대에게 또 지는
    것도 새 정보가 아니다."""
    got = _deltas([(1, 2, "team1", 3 * i) for i in range(6)], focal=2)
    assert all(g < 0 for g in got), got
    assert abs(got[4]) < abs(got[0]) * 0.1, got


def test_a_different_opponent_pays_full():
    """상대를 바꿔 가며 이기면 감쇠가 안 걸린다 — 줄어드는 건 TrueSkill 몫뿐이다.

    여섯 판째를 견주면, 같은 상대만 잡은 쪽은 새 상대를 잡은 쪽의 몇십 분의 일이다."""
    varied = _deltas([(1, 10 + i, "team1", 3 * i) for i in range(6)], focal=1)
    same = _deltas([(1, 2, "team1", 3 * i) for i in range(6)], focal=1)
    assert varied[5] > varied[0] * 0.3, varied      # TrueSkill 자체 감소만
    assert same[5] < varied[5] * 0.05, (same, varied)


def test_a_long_rest_brings_the_value_back():
    """오래 안 붙으면 횟수가 반감기만큼 줄어 값이 되살아난다."""
    soon = _deltas([(1, 2, "team1", 0), (1, 2, "team1", 3)], focal=1)
    later = _deltas([(1, 2, "team1", 0), (1, 2, "team1", 180)], focal=1)
    assert later[1] > soon[1] * 2, (soon, later)


def test_an_upset_is_damped_too_so_rankings_do_not_invert():
    """세 판 내리 지다가 뒤집은 판도 감쇠에 걸린다 — 연승 기준이 아니라 맞대결 횟수 기준이다.

    이게 안 걸리면 가끔 이변을 내는 쪽이 매번 제값을 받아, 꾸준히 이기는 쪽을 점수로 앞선다."""
    same = _deltas(
        [(1, 2, "team1", 0), (1, 2, "team1", 3), (1, 2, "team1", 6), (1, 2, "team2", 9)],
        focal=2,
    )
    fresh = _deltas(
        [(1, 2, "team1", 0), (1, 2, "team1", 3), (1, 2, "team1", 6), (3, 2, "team2", 9)],
        focal=2,
    )
    assert same[3] > 0 and fresh[3] > 0
    assert same[3] < fresh[3] * 0.35, (same, fresh)
