"""σ 되돌림(drift) — 쉰 날수에 비례해 풀리고, 달력 경계와는 무관하다.

예전에는 달이 바뀌는 순간 전원에게 한 달치를 통째로 얹었다. 그래서 σ가 부풀어 있는 월초의
승리가 월말의 승리보다 값이 나갔다(실측 1.78배). 지금은 같은 양을 날마다 나눠 푼다 —
아래 테스트들이 지키는 성질이 그것이다.
"""
import math

from app.domain.game_results.rating import (
    SEASON_SIGMA,
    SEASON_VAR_PER_DAY,
    SIGMA0,
    Rating,
    RatingEngine,
)


def _played() -> RatingEngine:
    e = RatingEngine()
    for _ in range(10):
        e.update([1], [2], "team1")
    return e


def test_drift_is_additive_over_days():
    """30일을 한 번에 풀든 열흘씩 세 번에 나눠 풀든 결과가 같아야 한다.

    이게 깨지면 '월초에 몰아주기'와 다를 바 없어진다 — 어느 날에 걸쳐 쉬었는지가
    결과를 바꾸면 안 된다."""
    once, split = _played(), _played()
    once.drift((1,), 30)
    for _ in range(3):
        split.drift((1,), 10)
    assert math.isclose(once.get(1).sigma, split.get(1).sigma, rel_tol=1e-12)


def test_drift_of_thirty_days_equals_one_season_sigma():
    """30일치 = 예전의 월초 한 번 되돌림. 총량은 그대로 두고 푸는 방식만 바꾼 것이다."""
    e = _played()
    before = e.get(1).sigma
    e.drift((1,), 30)
    assert math.isclose(
        e.get(1).sigma, math.sqrt(before ** 2 + SEASON_SIGMA ** 2), rel_tol=1e-9
    )
    assert math.isclose(SEASON_VAR_PER_DAY * 30, SEASON_SIGMA ** 2, rel_tol=1e-9)


def test_drift_never_exceeds_initial_sigma():
    """아무리 오래 쉬어도 '처음 보는 사람'보다 더 모르는 상태는 없다."""
    e = _played()
    e.drift((1,), 3650)
    assert e.get(1).sigma == SIGMA0


def test_drift_leaves_mu_and_unplayed_members_alone():
    """실력 추정(μ)은 손대지 않고, 한 번도 안 뛴 사람은 애초에 되돌릴 것이 없다."""
    e = _played()
    mu = e.get(1).mu
    e.drift((1, 99), 30)
    assert e.get(1).mu == mu
    assert 99 not in e.rating
    assert e.get(99) == Rating()


def test_drift_of_zero_or_negative_days_is_a_no_op():
    """같은 날 연달아 둔 경기는 그 사이에 흐른 시간이 없다."""
    e = _played()
    sigma = e.get(1).sigma
    e.drift((1,), 0)
    e.drift((1,), -5)
    assert e.get(1).sigma == sigma
