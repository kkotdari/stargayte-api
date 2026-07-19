"""오프라인 검증: '전투력'을 무엇으로 매기는 게 실제 강함을 더 잘 반영하나?

세 가지 '전투력' 후보를 같은 경기 데이터로 순차 백테스트해서, 각자
"다음 경기 승자를 얼마나 잘 맞히나(예측 정확도)"로 비교한다. 예측 정확도는
"그 전투력이 실제 강함을 반영하나"의 객관적 척도다.

  - current : 현재 방식(결과 기반). 회원-회원 맞대결 순우열(우세수-열세수)로 강함을
              매기고, 팀 강함 합이 큰 팀이 이긴다고 예측. service.py의 strength 산식과 동일.
  - elo     : 팀 평균 Elo 레이팅(순차 누적). 레이팅 높은 팀이 이긴다고 예측(A안 프로토타입).
  - eapm    : 팀 평균 유효APM 높은 쪽이 이긴다고 예측(사용자 원안 검증용).
  - ecmd    : 팀 평균 유효커맨드/분 높은 쪽이 이긴다고 예측(사용자 원안 검증용).
  - base    : 항상 team1(선등록 팀) 승 예측 — 기준선(우연/편향 확인용).

정확도가 높을수록 그 지표가 '실제 강함'을 잘 담는다는 뜻이다. eapm/ecmd가 base와
비슷하면(=거의 안 맞히면) 그걸로 전투력을 매기면 안 된다는 근거가 된다.

실행:
  # 실제 DB (로컬/스테이징 등 .env의 DATABASE_URL로 접속)
  python -m scripts.rating_backtest --source db --match-type 0102
  python -m scripts.rating_backtest --source db          # 전체(개인전+팀전)
  # 합성 데이터 자체검증(엔진이 맞게 도는지 확인)
  python -m scripts.rating_backtest --source synthetic
"""
from __future__ import annotations

import argparse
import asyncio
import math
from collections import defaultdict
from dataclasses import dataclass, field

# 현재 방식과 같은 고정 스케일(service.py NET_SCALE_MAX).
NET_SCALE_MAX = 9.0
TEAM_MIN_SIZE = 2


@dataclass
class Player:
    member_id: int | None  # None = 컴퓨터/비회원(레이팅 갱신 대상 아님)
    race: str = ""
    eapm: float | None = None
    ecmd: float | None = None  # 분당 유효 커맨드


@dataclass
class Game:
    order: tuple  # 정렬키(시간순)
    match_type: str
    team1: list[Player]
    team2: list[Player]
    result: str  # "team1" | "team2" | "draw"


# ─────────────────────────── 지표별 예측기 ───────────────────────────

def _members(side: list[Player]) -> list[int]:
    return [p.member_id for p in side if p.member_id is not None]


def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


class EloModel:
    """팀 평균 Elo. 비회원 슬롯은 base 고정값으로 팀 평균에만 반영(갱신 안 함)."""

    def __init__(self, base: float = 1500.0, k_new: float = 40.0, k_est: float = 20.0,
                 new_until: int = 10) -> None:
        self.base = base
        self.k_new, self.k_est, self.new_until = k_new, k_est, new_until
        self.rating: dict[int, float] = defaultdict(lambda: base)
        self.games: dict[int, int] = defaultdict(int)

    def _team_avg(self, side: list[Player]) -> float | None:
        vals = [self.rating[p.member_id] if p.member_id is not None else self.base for p in side]
        return sum(vals) / len(vals) if vals else None

    def predict(self, g: Game) -> str | None:
        r1, r2 = self._team_avg(g.team1), self._team_avg(g.team2)
        if r1 is None or r2 is None:
            return None
        return "team1" if r1 >= r2 else "team2"

    def update(self, g: Game) -> None:
        r1, r2 = self._team_avg(g.team1), self._team_avg(g.team2)
        if r1 is None or r2 is None:
            return
        e1 = 1.0 / (1.0 + 10 ** ((r2 - r1) / 400.0))
        s1 = 1.0 if g.result == "team1" else 0.0 if g.result == "team2" else 0.5
        for side, delta in ((g.team1, s1 - e1), (g.team2, (1 - s1) - (1 - e1))):
            for p in side:
                if p.member_id is None:
                    continue
                k = self.k_new if self.games[p.member_id] < self.new_until else self.k_est
                self.rating[p.member_id] += k * delta
                self.games[p.member_id] += 1


class CurrentModel:
    """현재 방식 재현 — 회원-회원 순우열로 strength를 매기고, 팀 strength 합이 큰 팀을 예측."""

    def __init__(self) -> None:
        # a -> b -> [wins, losses] (a가 b에게)
        self.wl: dict[int, dict[int, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        self.seen: set[int] = set()

    def _strength(self, pk: int) -> float:
        row = self.wl.get(pk, {})
        sup = inf = 0
        for _opp, (w, l) in row.items():
            if w > l:
                sup += 1
            elif l > w:
                inf += 1
        net = sup - inf
        denom = max(1, len(self.seen) - 1)
        return 1 + NET_SCALE_MAX * max(0, net) / denom

    def predict(self, g: Game) -> str | None:
        m1, m2 = _members(g.team1), _members(g.team2)
        if not m1 or not m2:
            return None
        s1 = sum(self._strength(p) for p in m1)
        s2 = sum(self._strength(p) for p in m2)
        return "team1" if s1 >= s2 else "team2"

    def update(self, g: Game) -> None:
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


def metric_predict(g: Game, key: str) -> str | None:
    a = _mean([getattr(p, key) for p in g.team1 if p.member_id is not None])
    b = _mean([getattr(p, key) for p in g.team2 if p.member_id is not None])
    if a is None or b is None:
        return None
    return "team1" if a >= b else "team2"


# ─────────────────────────── 백테스트 ───────────────────────────

@dataclass
class Acc:
    correct: int = 0
    total: int = 0
    skipped: int = 0

    def add(self, pred: str | None, actual: str) -> None:
        if pred is None:
            self.skipped += 1
            return
        self.total += 1
        self.correct += int(pred == actual)

    @property
    def pct(self) -> float:
        return 100.0 * self.correct / self.total if self.total else 0.0


def backtest(games: list[Game], warmup_games: int = 6) -> dict:
    games = sorted(games, key=lambda g: g.order)
    elo, cur = EloModel(), CurrentModel()
    accs = {k: Acc() for k in ("current", "elo", "eapm", "ecmd", "base")}
    member_games: dict[int, int] = defaultdict(int)

    for g in games:
        decisive = g.result in ("team1", "team2")
        both_have_members = bool(_members(g.team1)) and bool(_members(g.team2))
        # 예측 평가는 '유의미한' 경기만: 결과 있음 + 양쪽 회원 존재 + 양팀 모두 워밍업 이후.
        warm = all(member_games[p] >= warmup_games for p in _members(g.team1) + _members(g.team2))
        if decisive and both_have_members and warm:
            accs["current"].add(cur.predict(g), g.result)
            accs["elo"].add(elo.predict(g), g.result)
            accs["eapm"].add(metric_predict(g, "eapm"), g.result)
            accs["ecmd"].add(metric_predict(g, "ecmd"), g.result)
            accs["base"].add("team1", g.result)
        # 상태 갱신(모든 경기)
        elo.update(g)
        cur.update(g)
        if decisive:
            for p in _members(g.team1) + _members(g.team2):
                member_games[p] += 1

    return {"accs": accs, "elo": elo, "cur": cur, "n_games": len(games)}


# ─────────────────────────── 리포트 ───────────────────────────

def print_report(res: dict, names: dict[int, str], win_stats: dict[int, tuple[int, int, int]]) -> None:
    accs = res["accs"]
    print("\n" + "=" * 64)
    print(f" 백테스트: 총 {res['n_games']}경기 (워밍업 이후 유의미 경기만 평가)")
    print("=" * 64)
    print("\n[예측 정확도] — '이 전투력이 다음 경기 승자를 얼마나 맞히나' (높을수록 실제 강함 반영)")
    order = ["elo", "current", "eapm", "ecmd", "base"]
    label = {"elo": "레이팅(Elo)", "current": "현재방식(결과)", "eapm": "유효APM",
             "ecmd": "유효커맨드/분", "base": "기준선(항상 team1)"}
    for k in order:
        a = accs[k]
        print(f"  {label[k]:<18} {a.pct:5.1f}%  ({a.correct}/{a.total} 적중, {a.skipped} 스킵)")

    print("\n[해석 가이드]")
    print("  · 레이팅 > 현재방식 이면 → A안이 실제 강함을 더 잘 반영(정규화 개선 근거).")
    print("  · 유효APM/커맨드가 기준선과 비슷 → 승패 예측력 약함 → 전투력 근거로 부적합.")

    elo: EloModel = res["elo"]
    rated = sorted(elo.rating.items(), key=lambda kv: kv[1], reverse=True)
    print("\n[레이팅 리더보드 상위 15]")
    print(f"  {'닉네임':<12} {'레이팅':>7} {'경기':>4} {'승-무-패':>10} {'승률':>6}")
    for pk, r in rated[:15]:
        w, d, l = win_stats.get(pk, (0, 0, 0))
        tot = w + d + l
        wr = f"{100*w/tot:.0f}%" if tot else "-"
        print(f"  {names.get(pk, str(pk))[:12]:<12} {r:7.0f} {elo.games[pk]:4d} {f'{w}-{d}-{l}':>10} {wr:>6}")

    # 레이팅 vs 승률 순위 상관(스피어만 근사) — 방향만 확인.
    common = [(pk, r) for pk, r in rated if sum(win_stats.get(pk, (0, 0, 0))) >= 5]
    if len(common) >= 3:
        wr = {pk: (win_stats[pk][0] / sum(win_stats[pk])) for pk, _ in common}
        rk_elo = {pk: i for i, (pk, _) in enumerate(sorted(common, key=lambda x: -x[1]))}
        rk_wr = {pk: i for i, (pk, _) in enumerate(sorted(common, key=lambda x: -wr[x[0]]))}
        n = len(common)
        d2 = sum((rk_elo[pk] - rk_wr[pk]) ** 2 for pk, _ in common)
        rho = 1 - 6 * d2 / (n * (n * n - 1)) if n > 1 else 0.0
        print(f"\n[레이팅 ↔ 승률 순위 상관(스피어만, 5경기+ {n}명)] rho = {rho:.3f}  (1에 가까울수록 일치)")


# ─────────────────────────── 데이터 소스 ───────────────────────────

def synthetic_games(n: int = 1200, seed: int = 7) -> tuple[list[Game], dict[int, str]]:
    """진짜 latent 실력을 심어놓고 경기를 생성 — 엔진이 그 실력 순서를 복원하는지 확인용.
    (Math.random 대신 결정적 LCG로 재현 가능.)"""
    true_skill = {i: 1000 + 90 * i for i in range(12)}  # 회원 12명, 실력 계단
    names = {i: f"P{i:02d}(skill{true_skill[i]})" for i in range(12)}
    state = seed

    def rnd() -> float:
        nonlocal state
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        return state / 0x7FFFFFFF

    def pick(k: int) -> list[int]:
        pool = list(range(12))
        out = []
        for _ in range(k):
            out.append(pool.pop(int(rnd() * len(pool))))
        return out

    games: list[Game] = []
    for t in range(n):
        size = 1 if rnd() < 0.5 else 2
        ids = pick(size * 2)
        t1, t2 = ids[:size], ids[size:]
        s1 = sum(true_skill[i] for i in t1) / size
        s2 = sum(true_skill[i] for i in t2) / size
        p1 = 1.0 / (1.0 + 10 ** ((s2 - s1) / 400.0))  # 실제 실력대로 승패 발생
        result = "team1" if rnd() < p1 else "team2"
        mk = lambda i: Player(member_id=i, eapm=true_skill[i] / 8 + rnd() * 40,
                              ecmd=true_skill[i] / 20 + rnd() * 10)
        games.append(Game(order=(t,), match_type="0101" if size == 1 else "0102",
                          team1=[mk(i) for i in t1], team2=[mk(i) for i in t2], result=result))
    return games, names


async def db_games(match_type: str | None) -> tuple[list[Game], dict[int, str]]:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.db.session import AsyncSessionLocal
    from app.domain.matches.models import Match
    from app.domain.members.models import Member, ReplayAlias

    async with AsyncSessionLocal() as s:
        alias_rows = (await s.execute(
            select(ReplayAlias.raw_name, ReplayAlias.member_pk).where(ReplayAlias.kind == "member")
        )).all()
        alias = {raw: pk for raw, pk in alias_rows}
        names = {pk: nick for pk, nick in (await s.execute(select(Member.pk, Member.nickname))).all()}

        q = select(Match).options(selectinload(Match.participants), selectinload(Match.result_row))
        if match_type:
            q = q.where(Match.match_type == match_type)
        matches = (await s.execute(q)).scalars().all()

    games: list[Game] = []
    for m in matches:
        rr = m.result_row
        if rr is None or rr.result not in ("team1", "team2", "draw"):
            continue  # 미실시 등 제외
        dur = rr.duration_seconds
        sides: dict[str, list[Player]] = {"team1": [], "team2": []}
        for p in m.participants:
            if p.team not in sides:
                continue
            ecmd = (p.effective_cmd_count / (dur / 60)) if (p.effective_cmd_count and dur) else None
            sides[p.team].append(Player(member_id=alias.get(p.player_name), race=p.race,
                                        eapm=p.eapm, ecmd=ecmd))
        order = (rr.game_started_at or m.match_date, m.match_no)
        games.append(Game(order=order, match_type=m.match_type,
                          team1=sides["team1"], team2=sides["team2"], result=rr.result))
    return games, names


def win_stats_of(games: list[Game]) -> dict[int, tuple[int, int, int]]:
    """회원별 (승, 무, 패) — 리더보드/상관 표시용(팀 결과를 각 회원에게 귀속)."""
    st: dict[int, list[int]] = defaultdict(lambda: [0, 0, 0])
    for g in games:
        for side, key in ((g.team1, "team1"), (g.team2, "team2")):
            for p in side:
                if p.member_id is None:
                    continue
                if g.result == "draw":
                    st[p.member_id][1] += 1
                elif g.result == key:
                    st[p.member_id][0] += 1
                else:
                    st[p.member_id][2] += 1
    return {k: tuple(v) for k, v in st.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["db", "synthetic"], default="synthetic")
    ap.add_argument("--match-type", default=None, help="0101=개인전, 0102=팀전, 미지정=전체")
    ap.add_argument("--warmup", type=int, default=6, help="각 회원 이 경기수 이후부터 예측 평가")
    args = ap.parse_args()

    if args.source == "db":
        games, names = asyncio.run(db_games(args.match_type))
    else:
        games, names = synthetic_games()

    if not games:
        print("경기 데이터가 없습니다.")
        return
    res = backtest(games, warmup_games=args.warmup)
    print_report(res, names, win_stats_of(games))


if __name__ == "__main__":
    main()
