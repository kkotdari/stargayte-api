"""레이팅 엔진 — 경기 결과로 회원별 실력(μ)과 불확실성(σ)을 추정한다.

라이브러리(trueskill 패키지는 빌드가 깨져 미사용) 없이 직접 구현한다. 표준정규 pdf/cdf만
필요하고 math.erf로 충분하다(scipy 불필요).

TrueSkill과 같은 계열이되 **사람들 사이의 상관까지 들고 간다**. 흔한 구현은 사람마다 σ를
하나씩만 두는데(대각 근사), 그러면 "A와 B의 격차는 확실히 아는데 둘의 절대 수준은 모른다"는
상태를 표현할 방법이 없다. 우리 클럽은 모두가 모두와 고르게 붙지 않는다 — 어떤 둘은 백 판을
붙고 어떤 사람은 세 판만 뛴다(지적: "모든 사람이 모든 사람에 대해 균등하게 경기하는 상황이
아니고 특정 사람과만 많이 하고 어떤 사람은 적게나 안 할 수도 있기 때문에, 현재의 모든
데이터를 정합한 예상결과에 대한 점수를 부여하는 거야"). 대각 근사로는 그 사정을 못 담아,
같은 상대를 백 번 이겨도 값이 안 떨어지고 정보가 사람을 건너 퍼지지도 않는다.

공분산 전체를 들고 가면 두 가지가 손대지 않아도 저절로 나온다:

  · **같은 상대 반복은 저절로 값이 떨어진다.** k번째 판은 정보의 1/k만 준다 — 실측으로
    같은 상대에게 8연승하면 54 → 27 → 17 → 12 → 9 → 7 → 5.7 → 4.8점. 예전에는 이걸
    맞대결 감쇠 인자(H2H_REPEAT_DAMP)로 손수 얹어야 했다.
  · **σ가 경기수에서 저절로 나온다.** 실측으로 93판 σ=2.98, 499판 σ=1.88, 892판 σ=1.84.
    예전에는 σ가 단조 감소해 점수 폭이 해마다 쪼그라드는 걸 달마다 인위적으로 되돌려야
    했다(SEASON_SIGMA). 이제 그 보정이 통째로 필요 없다(지적: "월 바뀌면서 인위적으로
    보정하는 것보다는 사람들 경기수에 비례해서 하는 뭔가 더 깔끔한 방법이 있지 않을까").

바뀐 뒤 실측(24명·월 250판·24개월, 점수순위와 실제 실력의 순위상관):
    대각 근사(예전)                 0.83
    대각 근사 + 맞대결 감쇠(예전)      0.96
    상관 모형(지금)                 0.99   상위 5명 5/5

핵심:
  · 각 회원 = μ(실력)와 서로 간의 공분산 한 벌. σ는 그 대각선의 제곱근이다.
  · 한 경기는 '이긴 편 합 − 진 편 합' 방향으로 전체 평균·공분산을 한 번에 갱신한다.
    그래서 안 뛴 사람의 μ도 조금 움직인다 — 내가 이긴 상대를 이긴 사람의 값이 같이 오르는,
    그 정보 전파가 이 모형의 요점이다.
  · 개인전/팀전은 호출부가 서로 다른 Engine 인스턴스를 써서 분리한다.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

# TrueSkill 표준 초기값(μ0=25). 소규모 클럽에 맞게 나중에 튜닝 가능.
MU0 = 25.0
SIGMA0 = MU0 / 3.0        # 8.333 — 초기 불확실성
# 경기당 실력 발휘 편차(스킬 폭). 이 값이 클수록 한 경기가 주는 정보가 적다고 보아 μ가
# 덜 움직인다. 기대승률을 정하는 t = μ격차 / √(dᵀΣd + nβ²)의 분모를 좌우한다 —
# 표준값(σ₀/2)은 우리 클럽에는 너무 예민했고, 한때 2σ₀까지 키웠더니 이번엔 μ격차가 분모에
# 묻혀 실력차를 거의 못 느꼈다(지적: "같은 사람에게 계속 이기는데 얻는 점수가 너무 안
# 줄어드는거 같은데"). 1σ₀가 그 사이다: μ격차 10이면 기대승률 74%, 20이면 90%.
BETA = SIGMA0 * 1.0      # 8.333
# 경기마다 되돌리는 불확실성 — '사람 실력은 시간이 지나면 변한다'는 몫이다. 이번에 뛴
# 사람의 분산에만 더한다(지적: "사람들 경기수에 비례해서"). 달력에 기대지 않으므로 월초·월말
# 같은 자리가 생기지 않는다.
#
# 이게 0이면 σ가 한없이 줄어 모형이 굳는다 — 실력이 달마다 조금씩 변하는 클럽으로 재보면
# 순위상관이 τ=0에서 0.970, σ₀/25에서 0.980으로 올라가고 그 위로는 더 안 오른다.
TAU = SIGMA0 / 25.0      # 0.333

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


class RatingEngine:
    """한 경기유형(개인전 또는 팀전)의 회원별 레이팅을 시간순으로 누적한다.

    member_id=None(컴퓨터/비회원) 슬롯은 자리를 아예 안 만든다 — 호출부가 그런 경기를
    통째로 건너뛴다.

    상태는 평균 벡터 하나와 공분산 행렬 하나다. 회원 수가 수십 명이라 조밀한 행렬로 들고
    가도 한 경기 갱신이 n² — 수천 판을 재생해도 눈 깜짝할 새다."""

    PROVISIONAL_GAMES = 5  # 이 미만이면 '잠정'

    def __init__(self) -> None:
        self._at: dict = {}                 # 키 → 행렬에서의 자리
        self._mu: list[float] = []
        self._cov: list[list[float]] = []   # 대칭 n×n
        self.games: dict = defaultdict(int)

    def _slot(self, key) -> int:
        """처음 보는 사람에게 자리를 하나 내준다 — μ0에, 아무와도 상관 없음(σ0²)."""
        at = self._at.get(key)
        if at is not None:
            return at
        at = len(self._mu)
        self._at[key] = at
        self._mu.append(MU0)
        for row in self._cov:
            row.append(0.0)
        self._cov.append([0.0] * at + [SIGMA0 ** 2])
        return at

    def clone(self) -> "RatingEngine":
        """지금 상태 그대로 한 벌 뜬다 — 여기서부터 뒤 경기만 이어 재생하려고 쓴다.

        기억해 둔 엔진을 곧바로 이어 쓰면, 그걸 이미 받아 간 쪽(다른 요청)의 μ·σ가 발밑에서
        바뀐다. 뜨는 값이 회원 수의 제곱이라 안 싼 일이지만, 한 경기 갱신 한 번어치일 뿐이라
        수천 판을 다시 도는 것에 비하면 아무것도 아니다."""
        twin = RatingEngine()
        twin._at = dict(self._at)
        twin._mu = list(self._mu)
        twin._cov = [row[:] for row in self._cov]
        twin.games = defaultdict(int, self.games)
        return twin

    def get(self, key) -> Rating:
        at = self._at.get(key)
        if at is None:
            return Rating()  # 아직 한 판도 안 뛴 사람(그리고 비회원)
        return Rating(self._mu[at], math.sqrt(max(self._cov[at][at], 1e-8)))

    def update(self, team1: list, team2: list, result: str) -> None:
        """team1/team2 중 이긴 편 기준으로 전체 평균·공분산을 한 번 갱신한다.

        방향 벡터 d는 '이긴 편 전원 +1, 진 편 전원 −1'이다. 표준 칼만 갱신 그대로:
            u  = Σd            (각자가 이 판에서 얼마나 흔들릴 수 있나)
            c² = dᵀΣd + nβ²    (이 판 결과의 총 분산)
            μ += u·v(t)/c ,  Σ −= u uᵀ·w(t)/c²
        u가 전체 벡터라 안 뛴 사람의 μ도 상관이 있는 만큼 따라 움직인다 — 그게 이 모형을
        쓰는 이유다."""
        m1 = [p for p in team1 if p is not None]
        m2 = [p for p in team2 if p is not None]
        if result not in ("team1", "team2") or not m1 or not m2:
            return  # 무승부/한쪽 편 없음 — 갱신 안 함
        won, lost = (m1, m2) if result == "team1" else (m2, m1)
        wi = [self._slot(p) for p in won]
        li = [self._slot(p) for p in lost]
        mu, cov = self._mu, self._cov
        n = len(mu)

        # 지난 경기 이후의 실력 변동(TAU) — 이번에 뛴 사람 몫만. σ0을 넘지는 않는다.
        for k in wi + li:
            cov[k][k] = min(SIGMA0 ** 2, cov[k][k] + TAU ** 2)

        # 1:1이 거의 전부라 그 경우만 따로 빠르게 — sum() 두 번 대신 뺄셈 하나다.
        if len(wi) == 1 and len(li) == 1:
            i, j = wi[0], li[0]
            u = [row[i] - row[j] for row in cov]
        else:
            u = [sum(row[a] for a in wi) - sum(row[a] for a in li) for row in cov]
        c2 = sum(u[a] for a in wi) - sum(u[a] for a in li) + (len(wi) + len(li)) * BETA ** 2
        c = math.sqrt(c2)
        t = (sum(mu[a] for a in wi) - sum(mu[a] for a in li)) / c
        v, w = _v_win(t), _w_win(t)

        step = v / c
        self._mu = mu = [m + x * step for m, x in zip(mu, u)]
        # Σ −= f·u uᵀ. 안쪽을 리스트 조립으로 도는 게 첨자 루프보다 몇 배 빠르다 —
        # 회원 수가 늘면 한 경기 갱신이 n²이라 이 한 줄이 재생 시간을 좌우한다.
        f = w / c2
        for a in range(n):
            ua = u[a] * f
            if ua == 0.0:
                continue
            row = cov[a]
            cov[a] = [r - ua * x for r, x in zip(row, u)]
        for a in range(n):  # 수치 오차로 분산이 음수로 내려가지 않게
            if cov[a][a] < 1e-8:
                cov[a][a] = 1e-8

        for p in won + lost:
            self.games[p] += 1

    def is_provisional(self, key) -> bool:
        return self.games.get(key, 0) < self.PROVISIONAL_GAMES
