"""[임시/분석] 전투력 산정 방식 백테스트 — 현재 방식(결과 순우열) vs 레이팅(팀 Elo).

각 방식으로 '다음 경기 승자'를 예측해 정확도로 '실제 강함 반영도'를 잰다. 예측 정확도가
높을수록 그 전투력이 실제 강함을 잘 담는다는 뜻. 관리자 임시 버튼에서 호출한다.
검증이 끝나면 이 파일과 라우터의 임시 엔드포인트를 함께 제거하면 된다.

eapm/유효커맨드 기반은 제외(폐기). 레이팅(Elo)과 현재방식만 비교한다.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

NET_SCALE_MAX = 9.0  # service.py와 동일


@dataclass
class Game:
    order: tuple
    match_type: str
    team1: list[int | None]  # member_pk 또는 None(컴퓨터/비회원)
    team2: list[int | None]
    result: str  # "team1" | "team2" | "draw"


def _members(side: list[int | None]) -> list[int]:
    return [p for p in side if p is not None]


class EloModel:
    def __init__(self, base=1500.0, k_new=40.0, k_est=20.0, new_until=10):
        self.base, self.k_new, self.k_est, self.new_until = base, k_new, k_est, new_until
        self.rating: dict[int, float] = defaultdict(lambda: base)
        self.games: dict[int, int] = defaultdict(int)

    def _avg(self, side):
        vals = [self.rating[p] if p is not None else self.base for p in side]
        return sum(vals) / len(vals) if vals else None

    def predict(self, g: Game):
        r1, r2 = self._avg(g.team1), self._avg(g.team2)
        if r1 is None or r2 is None:
            return None
        return "team1" if r1 >= r2 else "team2"

    def update(self, g: Game):
        r1, r2 = self._avg(g.team1), self._avg(g.team2)
        if r1 is None or r2 is None:
            return
        e1 = 1.0 / (1.0 + 10 ** ((r2 - r1) / 400.0))
        s1 = 1.0 if g.result == "team1" else 0.0 if g.result == "team2" else 0.5
        for side, delta in ((g.team1, s1 - e1), (g.team2, (1 - s1) - (1 - e1))):
            for p in side:
                if p is None:
                    continue
                k = self.k_new if self.games[p] < self.new_until else self.k_est
                self.rating[p] += k * delta
                self.games[p] += 1


class CurrentModel:
    def __init__(self):
        self.wl: dict[int, dict[int, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        self.seen: set[int] = set()

    def _strength(self, pk: int) -> float:
        sup = inf = 0
        for _opp, (w, l) in self.wl.get(pk, {}).items():
            if w > l:
                sup += 1
            elif l > w:
                inf += 1
        net = sup - inf
        denom = max(1, len(self.seen) - 1)
        return 1 + NET_SCALE_MAX * max(0, net) / denom

    def predict(self, g: Game):
        m1, m2 = _members(g.team1), _members(g.team2)
        if not m1 or not m2:
            return None
        s1 = sum(self._strength(p) for p in m1)
        s2 = sum(self._strength(p) for p in m2)
        return "team1" if s1 >= s2 else "team2"

    def strength_ranking(self) -> dict[int, int]:
        order = sorted(self.seen, key=lambda pk: -self._strength(pk))
        return {pk: i + 1 for i, pk in enumerate(order)}

    def update(self, g: Game):
        m1, m2 = _members(g.team1), _members(g.team2)
        for pk in m1 + m2:
            self.seen.add(pk)
        if g.result == "draw" or not m1 or not m2:
            return
        winners, losers = (m1, m2) if g.result == "team1" else (m2, m1)
        for w in winners:
            for l in losers:
                self.wl[w][l][0] += 1
                self.wl[l][w][1] += 1


def _acc(correct, total):
    return 100.0 * correct / total if total else 0.0


def render_report(games: list[Game], names: dict[int, str], warmup: int = 4) -> str:
    games = sorted(games, key=lambda g: g.order)
    elo, cur = EloModel(), CurrentModel()
    stat = {k: [0, 0] for k in ("elo", "current", "base")}  # [correct, total]
    win = defaultdict(lambda: [0, 0, 0])  # pk -> [w,d,l] (팀 결과 귀속)
    mg = defaultdict(int)

    for g in games:
        decisive = g.result in ("team1", "team2")
        both = bool(_members(g.team1)) and bool(_members(g.team2))
        warm = all(mg[p] >= warmup for p in _members(g.team1) + _members(g.team2))
        if decisive and both and warm:
            for key, model in (("elo", elo), ("current", cur)):
                pred = model.predict(g)
                if pred is not None:
                    stat[key][1] += 1
                    stat[key][0] += int(pred == g.result)
            stat["base"][1] += 1
            stat["base"][0] += int(g.result == "team1")
        for side, tk in ((g.team1, "team1"), (g.team2, "team2")):
            for p in _members(side):
                if g.result == "draw":
                    win[p][1] += 1
                elif g.result == tk:
                    win[p][0] += 1
                else:
                    win[p][2] += 1
        elo.update(g)
        cur.update(g)
        if decisive:
            for p in _members(g.team1) + _members(g.team2):
                mg[p] += 1

    cur_rank = cur.strength_ranking()
    L = []
    L.append("=" * 60)
    L.append(f" 레이팅 백테스트 — 총 {len(games)}경기")
    L.append("=" * 60)
    L.append("")
    L.append("[예측 정확도] 다음 경기 승자 적중률 (높을수록 실제 강함 반영)")
    lab = {"elo": "레이팅(Elo)", "current": "현재방식(결과)", "base": "기준선(항상 team1)"}
    for k in ("elo", "current", "base"):
        c, t = stat[k]
        L.append(f"  {lab[k]:<16} {_acc(c, t):5.1f}%  ({c}/{t})")
    L.append("")
    L.append("[레이팅 리더보드]  (현재순위→레이팅순위 이동, 승률=이 경기유형 전적)")
    L.append(f"  {'레이팅#':>5} {'현재#':>5}  {'닉네임':<12} {'경기':>4} {'승-무-패':>9} {'승률':>5} {'레이팅':>6}")
    rated = sorted(elo.rating.items(), key=lambda kv: -kv[1])
    for i, (pk, r) in enumerate(rated):
        w, d, l = win[pk]
        tot = w + d + l
        wr = f"{100*w/tot:.0f}%" if tot else "-"
        nick = names.get(pk, f"#{pk}")[:12]
        L.append(f"  {i+1:>5} {cur_rank.get(pk, '-'):>5}  {nick:<12} {elo.games[pk]:>4} "
                 f"{f'{w}-{d}-{l}':>9} {wr:>5} {r:>6.0f}")

    # 레이팅↔승률 순위 상관(스피어만) — 방향 확인용.
    common = [pk for pk, _ in rated if sum(win[pk]) >= 5]
    if len(common) >= 3:
        wr = {pk: win[pk][0] / sum(win[pk]) for pk in common}
        e_rank = {pk: i for i, pk in enumerate(sorted(common, key=lambda p: -elo.rating[p]))}
        w_rank = {pk: i for i, pk in enumerate(sorted(common, key=lambda p: -wr[p]))}
        n = len(common)
        d2 = sum((e_rank[pk] - w_rank[pk]) ** 2 for pk in common)
        rho = 1 - 6 * d2 / (n * (n * n - 1))
        L.append("")
        L.append(f"[레이팅 ↔ 승률 순위 상관(5경기+, {n}명)] rho = {rho:.3f} (1이면 완전일치)")
    return "\n".join(L)
