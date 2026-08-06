"""TrueSkill 레이팅 엔진 — 경기 결과로 회원별 실력(μ)과 불확실성(σ)을 추정한다.

라이브러리(trueskill 패키지는 빌드가 깨져 미사용) 없이 2팀/1:1 케이스를 직접 구현한다.
표준정규 pdf/cdf만 필요하고 math.erf로 충분하다(scipy 불필요).

핵심:
  · 각 회원 = (μ, σ). 강한 상대를 이길수록 μ가 많이 오른다.
  · 경기가 적으면 σ가 커서 값이 '잠정'. 순위는 보수 추정치(μ − 3σ)로 매겨 소수표본 인플레를 막는다.
  · 팀 결과는 팀 실력합으로 기대승률을 내고, 각자 σ 비중만큼 μ가 갱신된다(팀→개인 분해).
  · 개인전/팀전은 호출부가 서로 다른 Engine 인스턴스를 써서 분리한다.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

# TrueSkill 표준 초기값(μ0=25). 소규모 클럽에 맞게 나중에 튜닝 가능.
MU0 = 25.0
SIGMA0 = MU0 / 3.0        # 8.333 — 초기 불확실성
# 경기당 실력 발휘 편차(스킬 폭). 이 값이 클수록 한 경기가 주는 정보가 적다고 보아 σ가
# 천천히 줄고 μ도 덜 움직인다. 초기 conservative(μ0−3σ0 = 0)는 β와 무관해 그대로라,
# 경기이력 Δ 합=카드점수 telescoping은 β를 어떻게 잡든 유지된다.
#
# 한때 SIGMA0*2.0(16.667)까지 키웠었다 — 소수 경기 선수가 3판 전승만으로 상위권에 치솟는 걸
# 막으려고 σ 감소를 늦춘 것이다(요청: "σ 천천히 감소"). 그 사정은 이제 없어졌다: 카드/순위
# 점수가 그때의 보수레이팅(μ−3σ) 원값이 아니라 경기별 Δ를 누적한 값(service의 running)으로
# 바뀌어, 판수에 단조증가한다. 3판 뛴 사람은 3판어치만 쌓여 애초에 위로 못 올라간다
# (실측: β를 절반으로 내려도 3연승 신입은 12명 중 최하위권).
#
# 대신 큰 β가 남긴 부작용이 드러났다(지적: "같은 사람에게 계속 이기는데 얻는 점수가 너무
# 안 줄어드는 거 같은데"). 기대승률을 정하는 t = μ격차 / √(2σ² + 2β²)에서 β=2σ₀면 분모의
# 85%를 β가 차지해 μ격차가 묻힌다 — μ가 20이나 벌어져도 시스템은 승률 78%로밖에 안 본다.
# 그래서 같은 상대를 계속 이겨도 한 판의 값이 잘 안 떨어졌다.
#
# 실측(12명·8개월·240판 뒤 같은 상대에게 6연승, 매 판 표시점수):
#   β=2σ₀   18.5 16.3 14.4 12.9 11.7 10.6  → 4판째 30% 감소, 패/승 무게비 0.42
#   β=1σ₀   22.7 17.7 14.2 11.8 10.0  8.6  → 4판째 48% 감소, 패/승 무게비 0.29
#   β=σ₀/2  24.4 16.2 11.8  9.0  7.2  5.9  → 4판째 63% 감소, 패/승 무게비 0.21
# 더 내리면 감소는 더 가팔라지지만 패배 한 판의 무게도 같이 가벼워져(표시점수의 σ항이 승패
# 양쪽을 다 밀어올린다) "지든 말든 많이 뛰면 쌓인다"에 가까워진다. 1σ₀가 그 사이의 자리다.
BETA = SIGMA0 * 1.0      # 8.333 — 같은 상대 연승 시 점수가 제대로 줄어들도록
TAU = SIGMA0 / 100.0      # 0.0833 — 시간에 따른 실력 변동(매 경기 σ²에 더함)
# 달이 바뀔 때 되돌리는 불확실성(요청: 월별 제로베이스 대신 지난달까지의 누적치로 상대강도를
# 계산해서 평가를 시작). μ(실력 추정)는 그대로 이월하고 σ만 이만큼 회복시킨다.
#
# 이월만 하고 이 되돌리기가 없으면 σ가 단조 감소해서 달마다의 점수 폭이 계속 쪼그라든다 —
# 실측(24명·월 250판·24개월)으로 그 달 1위 점수가 1월 230점 → 6월 28점 → 12월 10점까지
# 내려갔다. 상대강도는 정확해지지만 작년 1월과 올해 1월을 견줄 수 없게 된다는 뜻이다.
# σ0/2를 되돌리면 같은 조건에서 230 → 114 → 102 → 187점으로 자리를 잡는다(σ가 4.7 근처에서
# 평형). 되돌린 뒤에도 σ0을 넘지는 않는다 — '처음 보는 사람'보다 더 모르는 상태는 없다.
SEASON_SIGMA = SIGMA0 / 2.0  # 4.167 — 월초 σ 되돌림 폭

_SQRT2 = math.sqrt(2.0)
_SQRT2PI = math.sqrt(2.0 * math.pi)


def _pdf(x: float) -> float:
    return math.exp(-x * x / 2.0) / _SQRT2PI


def _cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def _v_win(t: float) -> float:
    """이긴 쪽 평균 이동 계수 = pdf(t)/cdf(t). t가 크게 음수면 수치적으로 -t로 수렴."""
    denom = _cdf(t)
    if denom < 1e-12:
        return -t
    return _pdf(t) / denom


def _w_win(t: float) -> float:
    v = _v_win(t)
    w = v * (v + t)
    return min(1.0, max(0.0, w))


@dataclass
class Rating:
    mu: float = MU0
    sigma: float = SIGMA0

    @property
    def conservative(self) -> float:
        """리더보드/순위용 보수 추정치 — 아직 불확실하면(σ 큼) 낮게 잡힌다."""
        return self.mu - 3.0 * self.sigma


def _update(win: list[Rating], lose: list[Rating]) -> tuple[list[Rating], list[Rating]]:
    """win 팀이 lose 팀을 이겼을 때 각 구성원의 새 Rating. (무승부 제외)"""
    a = [(r.mu, r.sigma ** 2 + TAU ** 2) for r in win]
    b = [(r.mu, r.sigma ** 2 + TAU ** 2) for r in lose]
    mu_a, mu_b = sum(m for m, _ in a), sum(m for m, _ in b)
    var_a, var_b = sum(v for _, v in a), sum(v for _, v in b)
    n = len(a) + len(b)
    c2 = var_a + var_b + n * BETA ** 2
    c = math.sqrt(c2)
    t = (mu_a - mu_b) / c
    v, w = _v_win(t), _w_win(t)
    new_win = [Rating(m + (var / c) * v, math.sqrt(max(var * (1.0 - (var / c2) * w), 1e-4)))
               for (m, var) in a]
    new_lose = [Rating(m - (var / c) * v, math.sqrt(max(var * (1.0 - (var / c2) * w), 1e-4)))
                for (m, var) in b]
    return new_win, new_lose


class RatingEngine:
    """한 경기유형(개인전 또는 팀전)의 회원별 레이팅을 시간순으로 누적한다.

    member_id=None(컴퓨터/비회원) 슬롯은 고정 기본 레이팅으로 경기 계산엔 넣되 갱신하지 않는다."""

    PROVISIONAL_GAMES = 5  # 이 미만이면 '잠정'

    def __init__(self) -> None:
        self.rating: dict[int, Rating] = {}
        self.games: dict[int, int] = defaultdict(int)

    def get(self, pk: int | None) -> Rating:
        if pk is None:
            return Rating()  # 비회원: 고정 기본값
        return self.rating.get(pk, Rating())

    def team_mu(self, side: list[int | None]) -> float:
        return sum(self.get(p).mu for p in side)

    def predict(self, team1: list[int | None], team2: list[int | None]) -> str | None:
        m1 = [p for p in team1 if p is not None]
        m2 = [p for p in team2 if p is not None]
        if not m1 or not m2:
            return None
        return "team1" if self.team_mu(team1) >= self.team_mu(team2) else "team2"

    def update(self, team1: list[int | None], team2: list[int | None], result: str) -> None:
        m1 = [p for p in team1 if p is not None]
        m2 = [p for p in team2 if p is not None]
        if result not in ("team1", "team2") or not m1 or not m2:
            return  # 무승부/한쪽 회원 없음 — 갱신 안 함
        win_ids, lose_ids = (team1, team2) if result == "team1" else (team2, team1)
        win_r = [self.get(p) for p in win_ids]
        lose_r = [self.get(p) for p in lose_ids]
        nw, nl = _update(win_r, lose_r)
        for pk, r in zip(win_ids, nw):
            if pk is not None:
                self.rating[pk] = r
                self.games[pk] += 1
        for pk, r in zip(lose_ids, nl):
            if pk is not None:
                self.rating[pk] = r
                self.games[pk] += 1

    def is_provisional(self, pk: int) -> bool:
        return self.games.get(pk, 0) < self.PROVISIONAL_GAMES

    def season_reset(self) -> None:
        """달이 바뀔 때 σ만 SEASON_SIGMA만큼 되돌린다 — μ는 손대지 않는다(위 상수 주석).

        점수를 깎는 것이 아니라 '이 달에 얼마나 움직일 수 있나'를 되살리는 일이다. 이 호출
        자체는 어떤 Δ도 만들지 않는다: 재생 루프가 Δ를 경기 갱신 앞뒤의 차이로만 재고,
        되돌리기는 두 경기 사이에서 일어나기 때문이다(_replay_ratings 참고). 그래서 카드
        점수와 경기 이력 Δ의 합은 되돌린 뒤에도 계속 맞는다."""
        for key, r in self.rating.items():
            self.rating[key] = Rating(
                r.mu, min(SIGMA0, math.sqrt(r.sigma ** 2 + SEASON_SIGMA ** 2))
            )
