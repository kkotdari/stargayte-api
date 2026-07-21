from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.domain.leagues.models import League, LeagueMatch, LeagueMatchSubstitution, LeagueTeam, LeagueTeamMember
from app.domain.leagues.repository import LeagueRepository
from app.domain.leagues.schemas import (
    LeagueBracketGenerateIn,
    LeagueCreateIn,
    LeagueListItemOut,
    LeagueListOut,
    LeagueMatchOut,
    LeagueMatchResultIn,
    LeagueBracketSeedIn,
    LeagueMatchScheduleIn,
    LeagueMatchSlotIn,
    LeagueMatchSubstitutionOut,
    LeagueMatchTeamRefOut,
    LeagueOut,
    LeagueRosterMemberOut,
    LeagueTeamOut,
    LeagueTeamRosterIn,
    LeagueUpdateIn,
)
from app.domain.members.models import Member
from app.domain.members.repository import MemberRepository

import string

# 팀/선수/대진표 규모는 상한 없이 무제한이다(요청: "팀수 무제한 개인전 선수 무제한
# 대진표 슬롯 무제한"). 라벨은 A, B, ... Z, AA, AB, ...처럼 스프레드시트 열 이름
# 방식으로 26개를 넘어가도 계속 이어진다.
def _team_label(index: int) -> str:
    letters = string.ascii_uppercase
    label = ""
    n = index
    while True:
        n, r = divmod(n, 26)
        label = letters[r] + label
        if n == 0:
            return label
        n -= 1


def _next_pow2(n: int) -> int:
    p = 1
    while p < n:
        p *= 2
    return p


def _total_rounds(draw_size: int) -> int:
    return draw_size.bit_length() - 1


def _status_of(league: League) -> str:
    """setup(대진표 미생성)/active/completed 3단계 — Challenge와 같은 원칙으로 계산만
    하고 저장하지 않는다. 완료 판정은 결승(가장 마지막 라운드) 경기에 승자가 들어왔는지만
    본다 — 결승은 참가 팀이 2팀 이상인 한 항상 정확히 1경기이고, 부전승 연쇄로 결승까지
    죽어있는(is_dead) 경우는 구조적으로 있을 수 없다(팀이 최소 2개 있어야 대진표를 만들
    수 있으므로)."""
    if league.draw_size is None:
        return "setup"
    total_rounds = _total_rounds(league.draw_size)
    final = next((m for m in league.matches if m.round == total_rounds), None)
    if final is not None and final.winner_team_id is not None:
        return "completed"
    return "active"


def _to_roster_member_out(ltm: LeagueTeamMember) -> LeagueRosterMemberOut:
    return LeagueRosterMemberOut(
        memberId=ltm.member.id,
        nickname=ltm.member.nickname,
        battletag=ltm.member.battletag,
        avatar=ltm.member.avatar_url,
        position=ltm.position,
    )


def to_team_out(team: LeagueTeam) -> LeagueTeamOut:
    return LeagueTeamOut(
        id=team.id, label=team.label,
        roster=[_to_roster_member_out(m) for m in team.roster],
    )


def _team_ref(team: LeagueTeam | None) -> LeagueMatchTeamRefOut | None:
    if team is None:
        return None
    return LeagueMatchTeamRefOut(id=team.id, label=team.label)


def _to_sub_out(sub: LeagueMatchSubstitution) -> LeagueMatchSubstitutionOut:
    return LeagueMatchSubstitutionOut(
        teamId=sub.team_id,
        rosterPosition=sub.roster_position,
        substituteMemberId=sub.substitute.id,
        substituteNickname=sub.substitute.nickname,
        note=sub.note,
    )


def to_match_out(match: LeagueMatch) -> LeagueMatchOut:
    return LeagueMatchOut(
        id=match.id, round=match.round, slotInRound=match.slot_in_round,
        teamA=_team_ref(match.team_a), teamB=_team_ref(match.team_b),
        isDead=match.is_dead, scheduledAt=match.scheduled_at,
        setsWonA=match.sets_won_a, setsWonB=match.sets_won_b,
        winnerTeamId=match.winner_team_id,
        substitutions=[_to_sub_out(s) for s in match.substitutions],
    )


def to_league_out(league: League) -> LeagueOut:
    return LeagueOut(
        id=league.id, name=league.name, mode=league.mode, bestOf=league.best_of,
        status=_status_of(league), drawSize=league.draw_size, plannedTeams=league.planned_teams,
        bracketLocked=league.bracket_locked_at is not None,
        teams=[to_team_out(t) for t in league.teams],
        matches=[to_match_out(m) for m in league.matches],
        createdAt=league.created_at,
    )


def to_list_item_out(league: League) -> LeagueListItemOut:
    return LeagueListItemOut(
        id=league.id, name=league.name, mode=league.mode,
        status=_status_of(league), teamCount=len(league.teams),
    )


class LeagueService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = LeagueRepository(session)
        self._member_repo = MemberRepository(session)

    async def _get_or_404(self, league_id: int) -> League:
        league = await self._repo.get(league_id)
        if league is None:
            raise NotFoundError("리그를 찾을 수 없습니다.")
        return league

    def _get_team_or_404(self, league: League, team_id: int) -> LeagueTeam:
        team = next((t for t in league.teams if t.id == team_id), None)
        if team is None:
            raise NotFoundError("팀을 찾을 수 없습니다.")
        return team

    def _get_match_or_404(self, league: League, match_id: int) -> LeagueMatch:
        match = next((m for m in league.matches if m.id == match_id), None)
        if match is None:
            raise NotFoundError("경기를 찾을 수 없습니다.")
        return match

    async def _refresh_match_relations(self, matches: list[LeagueMatch]) -> None:
        """team_a_id/team_b_id를 (관계 속성이 아니라) 원시 FK 컬럼으로 직접 바꾸는 곳들
        (_propagate_winner/clear_match_result의 cascade)이 있어, 그 즉시 team_a/team_b
        관계 속성이 자동으로 갱신되지 않는다 — SQLAlchemy는 컬럼→관계 방향 동기화를
        자동으로 해주지 않고, 관계 속성은 다음에 실제로 새로 로드될 때만 최신 값을
        반영한다. 응답 직렬화(to_match_out) 전에 항상 명시적으로 새로고침해 이 자리에서
        오래된 team_a/team_b가 그대로 노출되는 걸 막는다."""
        for m in matches:
            await self._session.refresh(m, attribute_names=["team_a", "team_b", "substitutions"])

    def _team_has_decided_match(self, league: League, team_id: int) -> bool:
        """이 팀이 이미 "실제로 치른" 경기 결과가 난 적이 있는지 — 있으면 팀 삭제/로스터
        변경으로 이미 확정된 대진 이력을 건드리게 되므로 막는다. 부전승으로만 이긴
        경우는 세지 않는다(요청: "A팀이 수정 불가능한 문제가 있음" — 상대가 구조적으로
        없었을 뿐 실제로 아무도 안 붙어봤는데 로스터가 잠기는 건 과했다). 실제 결과가
        입력된 경기만 sets_won_a가 채워진다(부전승 자동 처리는 세트 스코어를 남기지
        않는다) — 그래서 winner_team_id 대신 sets_won_a로 구분한다. 대진표 생성 전
        (draw_size is None)에는 애초에 어떤 경기도 없어 항상 False다."""
        return any(
            m.sets_won_a is not None and (m.team_a_id == team_id or m.team_b_id == team_id)
            for m in league.matches
        )

    async def list_leagues(self) -> LeagueListOut:
        leagues = await self._repo.list_all()
        return LeagueListOut(items=[to_list_item_out(l) for l in leagues])

    async def get_league(self, league_id: int) -> LeagueOut:
        league = await self._get_or_404(league_id)
        return to_league_out(league)

    async def create_league(self, payload: LeagueCreateIn, *, actor: Member) -> LeagueOut:
        league = League(
            name=payload.name, mode=payload.mode, best_of=payload.best_of,
            created_by=actor.pk, updated_by=actor.pk,
        )
        self._repo.add(league)
        await self._repo.flush()
        await self._session.commit()
        await self._session.refresh(league, attribute_names=["teams", "matches"])
        return to_league_out(league)

    async def update_league(
        self, league_id: int, payload: LeagueUpdateIn, *, actor: Member,
    ) -> LeagueOut:
        league = await self._get_or_404(league_id)
        if league.draw_size is not None:
            raise ValidationError("대진표 생성 후에는 리그 설정을 바꿀 수 없습니다.")
        if payload.name is not None:
            league.name = payload.name
        if payload.best_of is not None:
            league.best_of = payload.best_of
        league.updated_by = actor.pk
        await self._session.commit()
        return to_league_out(league)

    async def delete_league(self, league_id: int) -> None:
        league = await self._get_or_404(league_id)
        await self._session.delete(league)
        await self._session.commit()

    async def add_team(self, league_id: int, *, actor: Member) -> LeagueTeamOut:
        league = await self._get_or_404(league_id)
        # 팀/선수 수 자체는 상한이 없다(요청: "팀수 무제한 개인전 선수 무제한"). 다만
        # 대진표가 이미 생성돼 있으면 그때 예약해둔 자리(planned_teams)만큼만 — 초과分은
        # 애초에 대진판에 자리가 없다(요청: "대진표는 팀이 있건 없건 생성 가능하게, 팀수
        # 미리 설정 가능" — 예약된 자리는 나중에 슬롯 배정으로 채운다).
        if league.draw_size is not None and len(league.teams) >= (league.planned_teams or 0):
            raise ValidationError("이 대진표에 예약된 자리가 다 찼습니다.")
        team = LeagueTeam(
            league_id=league.id, label=_team_label(len(league.teams)),
            created_by=actor.pk, updated_by=actor.pk,
        )
        league.teams.append(team)
        await self._repo.flush()
        await self._session.commit()
        await self._session.refresh(team, attribute_names=["roster"])
        return to_team_out(team)

    async def delete_team(self, league_id: int, team_id: int, *, actor: Member) -> LeagueOut:
        league = await self._get_or_404(league_id)
        team = self._get_team_or_404(league, team_id)
        # 대진표 생성 자체는 더 이상 팀 삭제를 막지 않는다(요청 3 — 팀 없이도 대진표를
        # 먼저 만들 수 있게 됐으니, 그 반대인 "팀 취소"도 자유로워야 앞뒤가 맞는다). 다만
        # 이미 결과가 난 경기에 참가했던 팀은 이력이 깨지므로 막는다. 팀이 어떤 경기의
        # 슬롯에 배정돼 있었다면 DB의 ON DELETE SET NULL이 그 슬롯을 자동으로 비운다.
        if self._team_has_decided_match(league, team_id):
            raise ValidationError("이미 결과가 나온 경기에 참가한 팀은 삭제할 수 없습니다.")
        league.teams.remove(team)
        await self._session.flush()
        # 라벨 재정렬 — 한 팀씩 순서대로 flush해 UniqueConstraint(league_id, label)와
        # 일시적으로 충돌하지 않게 한다(예: C→B로 옮길 때 기존 B가 먼저 비워져 있어야 함).
        # 라벨이 26개(Z)를 넘어가면 여러 글자(AA, AB..)가 섞이는데, 문자열 그대로
        # 정렬하면 "AA" < "B"가 돼버려(길이 다른 문자열의 사전식 비교) 순서가 깨진다 —
        # (길이, 문자열) 튜플로 정렬해야 원래 부여 순서(A..Z, AA..)가 유지된다.
        remaining = sorted(league.teams, key=lambda t: (len(t.label), t.label))
        for i, t in enumerate(remaining):
            new_label = _team_label(i)
            if t.label != new_label:
                t.label = new_label
                t.updated_by = actor.pk
                await self._session.flush()
        await self._session.commit()
        await self._session.refresh(league, attribute_names=["teams"])
        return to_league_out(league)

    async def set_roster(
        self, league_id: int, team_id: int, payload: LeagueTeamRosterIn, *, actor: Member,
    ) -> LeagueTeamOut:
        league = await self._get_or_404(league_id)
        team = self._get_team_or_404(league, team_id)
        # delete_team과 같은 원칙 — 대진표 생성 여부가 아니라 "이미 결과가 난 경기에
        # 참가했는지"로 막는다(요청 3의 연장: 대진표 생성 후에도 새로 추가한 팀엔
        # 로스터를 넣을 수 있어야 한다).
        if self._team_has_decided_match(league, team_id):
            raise ValidationError("이미 결과가 나온 경기에 참가한 팀은 로스터를 바꿀 수 없습니다.")
        if league.mode == "individual" and len(payload.member_ids) != 1:
            raise ValidationError("개인리그는 로스터를 1명으로만 구성할 수 있습니다.")

        members: list[Member] = []
        for member_id in payload.member_ids:
            m = await self._member_repo.get_by_login_id(member_id)
            if m is None:
                raise NotFoundError(f"존재하지 않는 회원입니다: {member_id}")
            members.append(m)

        other_team_pks = {
            ltm.member_pk for t in league.teams if t.id != team_id for ltm in t.roster
        }
        dup = [m.nickname for m in members if m.pk in other_team_pks]
        if dup:
            raise ConflictError(f"이미 다른 팀에 속한 회원입니다: {', '.join(dup)}")

        # 먼저 기존 로스터를 지우고 flush한 뒤에 새로 넣는다 — 그대로 컬렉션을 교체하면
        # 안 바뀐 회원의 (league_id, member_pk) 유니크가 delete/insert 순서에 따라
        # 일시적으로 충돌할 수 있다.
        for ltm in list(team.roster):
            await self._session.delete(ltm)
        await self._session.flush()
        team.roster = [
            LeagueTeamMember(league_id=league.id, league_team_id=team.id, member_pk=m.pk, position=i)
            for i, m in enumerate(members)
        ]
        await self._session.commit()
        await self._session.refresh(team, attribute_names=["roster"])
        return to_team_out(team)

    async def generate_bracket(
        self, league_id: int, payload: LeagueBracketGenerateIn, *, actor: Member,
    ) -> LeagueOut:
        league = await self._get_or_404(league_id)
        if league.bracket_locked_at is not None:
            raise ConflictError("대진이 확정돼 규모를 바꿀 수 없습니다.")
        team_count = payload.team_count
        if team_count < len(league.teams):
            raise ValidationError("이미 만들어진 팀 수보다 적게는 잡을 수 없습니다.")

        # 팀수/대진표 규모는 상한이 없다(요청: "팀수 무제한 개인전 선수 무제한 대진표
        # 슬롯 무제한"). 이미 대진표가 있어도 규모를 다시 잡을 수 있다(요청: "팀수,
        # 대진표 슬롯 수 다 수정가능해야돼") — 단 실제 경기 결과가 하나라도 들어갔으면
        # 재생성이 그 진행 상황을 지워버리므로 막는다. 1라운드에 이미 배정해둔 팀은
        # 그대로 살리고 규모만 다시 잡는다(요청: "참가팀수 늘릴때 기존 지정된건
        # 리셋하지 말아줘") — 결과가 하나도 없다는 게 이미 위에서 보장되므로 2라운드
        # 이상은 전부 "미정"이었을 수밖에 없어, 그 라운드들만 구조가 바뀌는 김에 새로
        # 만든다.
        old_round1_by_slot: dict[int, LeagueMatch] = {}
        if league.draw_size is not None:
            if any(m.winner_team_id is not None for m in league.matches):
                raise ValidationError("이미 결과가 입력된 경기가 있어 대진표 규모를 바꿀 수 없습니다.")
            for m in list(league.matches):
                if m.round == 1:
                    old_round1_by_slot[m.slot_in_round] = m
                else:
                    league.matches.remove(m)
            await self._repo.flush()

        # 대진표는 빈 채로 만든다 — 지금 있는 팀을 자동으로 채워 넣지 않고, 어느 팀이
        # 어느 칸에 들어갈지는 관리자가 슬롯 API(set_match_slot)로 직접 정한다(요청:
        # "대진표 생성 누르면 빈 대진표가 생기고 각 칸에 누가 들어갈지 정할 수 있는
        # 시스템으로"). team_count(관리자가 미리 정한 규모) 미만 자리는 나중에 팀이
        # 배정될 수 있는 "예약"이고, 그 이상(draw_size까지의 패딩)만 구조적으로 영원히
        # 빈 자리(is_dead)다 — 부전승 자동 처리는 실제로 슬롯에 팀이 배정되는 순간
        # (set_match_slot)에 일어난다.
        draw_size = _next_pow2(team_count)
        total_rounds = _total_rounds(draw_size)
        league.draw_size = draw_size
        league.planned_teams = team_count
        league.updated_by = actor.pk

        # 부전승(bye)은 한 자리에 몰아넣지 않고 1라운드 앞쪽 슬롯부터 한 경기당 하나씩
        # 흩어서 배정한다(요청: "각 부전승을 팀별로 분산 배정" — 보통 시드 방식과 동일,
        # "마지막 시드를 부전승 처리"). 앞쪽 byes개 슬롯은 a자리만 실제 팀이 들어오고
        # b자리는 구조적으로 영원히 빈 자리, 나머지 슬롯은 양쪽 다 실제 경기가 필요하다.
        # byes(=draw_size-team_count)는 항상 draw_size//2보다 작다(다음 2의 거듭제곱을
        # 쓰므로 team_count가 항상 draw_size의 절반보다 큼) — 그래서 한 경기에 부전승이
        # 두 개 몰리는 일은 생기지 않고, 1라운드는 절대 완전히 죽지(is_dead) 않는다. 어느
        # 팀이 실제로 부전승을 받을지는 관리자가 슬롯 배정(set_match_slot) 때 앞쪽 슬롯의
        # a자리에 어떤 팀을 놓을지로 직접 정한다.
        dead: dict[int, list[bool]] = {1: [False] * (draw_size // 2)}
        for r in range(2, total_rounds + 1):
            dead[r] = [dead[r - 1][2 * s] and dead[r - 1][2 * s + 1] for s in range(draw_size // (2 ** r))]

        by_round_slot: dict[tuple[int, int], LeagueMatch] = {}
        for slot in range(draw_size // 2):
            m = old_round1_by_slot.pop(slot, None)
            if m is not None:
                m.is_dead = dead[1][slot]
                m.updated_by = actor.pk
            else:
                m = LeagueMatch(
                    league_id=league.id, round=1, slot_in_round=slot,
                    is_dead=dead[1][slot],
                    created_by=actor.pk, updated_by=actor.pk,
                )
                league.matches.append(m)
            by_round_slot[(1, slot)] = m
        # 규모가 줄어들어 더는 필요 없어진 1라운드 슬롯은(있었다면) 배정된 팀째로 버려진다.
        for leftover in old_round1_by_slot.values():
            league.matches.remove(leftover)

        for r in range(2, total_rounds + 1):
            count = draw_size // (2 ** r)
            for slot in range(count):
                m = LeagueMatch(
                    league_id=league.id, round=r, slot_in_round=slot,
                    is_dead=dead[r][slot],
                    created_by=actor.pk, updated_by=actor.pk,
                )
                league.matches.append(m)
                by_round_slot[(r, slot)] = m
        await self._repo.flush()
        # 방금 만든 LeagueMatch는 team_a/team_b/substitutions 관계가 아직 로드된 적이 없어
        # (session.get()으로 불러온 게 아니라 새로 만든 객체라 selectin이 자동 적용되지
        # 않는다), to_match_out에서 그대로 접근하면 비동기 세션 밖에서 지연 로딩이 걸려
        # MissingGreenlet 에러가 난다 — 미리 명시적으로 채워둔다.
        await self._refresh_match_relations(list(by_round_slot.values()))

        await self._session.commit()
        await self._session.refresh(league, attribute_names=["teams", "matches"])
        # 부전승 전파가 team_a_id/team_b_id를 관계 속성이 아니라 원시 FK 컬럼으로 직접
        # 바꿔서, 그 대상이 된 매치들의 team_a/team_b가 여전히 예전 값(비어있음)으로
        # 캐시돼 있을 수 있다 — 응답 직렬화 전에 다시 새로고침한다.
        await self._refresh_match_relations(league.matches)
        return to_league_out(league)

    def _propagate_winner(
        self, by_round_slot: dict[tuple[int, int], LeagueMatch], total_rounds: int,
        from_round: int, from_slot: int, winner_team_id: int,
    ) -> None:
        if from_round >= total_rounds:
            return
        next_round, next_slot = from_round + 1, from_slot // 2
        side = "team_a_id" if from_slot % 2 == 0 else "team_b_id"
        target = by_round_slot.get((next_round, next_slot))
        if target is None:
            return
        setattr(target, side, winner_team_id)
        self._maybe_auto_resolve(by_round_slot, total_rounds, target)

    def _maybe_auto_resolve_round1(
        self, league: League, by_round_slot: dict[tuple[int, int], LeagueMatch],
        total_rounds: int, match: LeagueMatch,
    ) -> None:
        """1라운드 전용 부전승 자동 처리 — set_match_slot으로 실제 팀이 슬롯에 배정되는
        순간 호출된다. 부전승은 앞쪽 byes(=draw_size-planned_teams)개 슬롯에 한 경기당
        하나씩 분산 배정돼 있다(generate_bracket 참고) — 이 슬롯이 그 부전승 자리이고
        한쪽만 채워졌다면 그 즉시 부전승 처리하고, 부전승 자리가 아니면(양쪽 다 실제
        경기가 필요한 자리) 한쪽만 채워졌어도 반대쪽 실제 팀 배정을 기다린다."""
        if match.is_dead or match.winner_team_id is not None:
            return
        a, b = match.team_a_id, match.team_b_id
        if a is not None and b is not None:
            return
        if a is None and b is None:
            return
        planned = league.planned_teams or 0
        byes = (league.draw_size or 0) - planned
        is_bye_slot = match.slot_in_round < byes
        winner = None
        if is_bye_slot:
            winner = a if a is not None else b
        if winner is not None:
            match.winner_team_id = winner
            match.result_entered_at = datetime.now(UTC)
            self._propagate_winner(by_round_slot, total_rounds, 1, match.slot_in_round, winner)

    def _maybe_auto_resolve(
        self, by_round_slot: dict[tuple[int, int], LeagueMatch], total_rounds: int, match: LeagueMatch,
    ) -> None:
        """반대쪽 자리가 영원히 안 채워지는 상태에서 한쪽만 채워지면 자동으로 부전승
        처리하고 다음 라운드로 전파한다. 2라운드 이상 전용이다(1라운드는 위
        _maybe_auto_resolve_round1이 따로 처리 — league.planned_teams가 있어야
        "비어있음"이 영구 공백인지 예약 자리인지 구분되는데, 그건 match 하나만 봐서는
        알 수 없어 league가 필요하다).

        2라운드 이상에서는 "비어있음"이 두 가지 뜻일 수 있다 — ①(그 자리를 먹이는
        이전 라운드 경기 자체가 is_dead라서) 영원히 안 채워지거나, ②아직 그 이전 라운드의
        실제 경기 결과를 기다리는 중이거나. ②인데 ①처럼 자동 부전승 처리해버리면, 부전승
        팀이 다음 라운드에서 실제 상대와 붙어야 하는데도 그걸 건너뛰고 계속 자동
        진출해버리는 버그가 생긴다(실제로 발생 확인 — 3팀 대진표에서 부전승 팀이 결승
        상대 없이 바로 우승 처리됨). 그래서 비어있는 쪽을 먹이는 이전 라운드 경기의
        is_dead를 직접 확인해, ①일 때만 자동 처리한다."""
        if match.round == 1:
            return  # generate_bracket이 leaf_present 기준으로 직접 처리 — 여기선 스킵.
        if match.is_dead or match.winner_team_id is not None:
            return
        a, b = match.team_a_id, match.team_b_id
        if a is not None and b is not None:
            return  # 양쪽 다 실제 팀 — 진짜 경기를 치러야 함, 자동 처리 대상 아님
        if a is None and b is None:
            return  # 둘 다 아직 None — is_dead가 아니므로 언젠가 실제 경기로 채워질 예정
        empty_child_slot = match.slot_in_round * 2 + (1 if a is not None else 0)
        feeder = by_round_slot.get((match.round - 1, empty_child_slot))
        if feeder is None or not feeder.is_dead:
            return  # 아직 실제 경기(위 ②) 결과를 기다리는 중 — 자동 처리하지 않는다
        winner = a if a is not None else b
        match.winner_team_id = winner
        match.result_entered_at = datetime.now(UTC)
        self._propagate_winner(by_round_slot, total_rounds, match.round, match.slot_in_round, winner)

    def _undo_decided(
        self, match: LeagueMatch, by_round_slot: dict[tuple[int, int], LeagueMatch],
        total_rounds: int, actor: Member,
    ) -> None:
        """이 경기의 결정(부전승이든 실제 결과든)을 취소하고, 거기서 다음 라운드로
        전파됐던 결과까지 재귀적으로 함께 취소한다. clear_match_result(공개 API,
        실제 결과만 취소 가능)와 set_match_slot(대진 확정 전 시드 변경 — 부전승 결정도
        취소 가능, 요청: "그전엔 부전승팀도 수정 가능해야해")이 같이 쓴다."""
        match.winner_team_id = None
        match.sets_won_a = None
        match.sets_won_b = None
        match.result_entered_by = None
        match.result_entered_at = None
        match.substitutions = []
        match.updated_by = actor.pk
        if match.round < total_rounds:
            next_round, next_slot = match.round + 1, match.slot_in_round // 2
            side = "team_a_id" if match.slot_in_round % 2 == 0 else "team_b_id"
            target = by_round_slot.get((next_round, next_slot))
            if target is not None:
                setattr(target, side, None)
                if target.winner_team_id is not None:
                    self._undo_decided(target, by_round_slot, total_rounds, actor)

    async def confirm_bracket(self, league_id: int, *, actor: Member) -> LeagueOut:
        """대진(시드)을 확정한다 — 그 뒤로는 set_match_slot으로 1라운드 시드를 더는
        바꿀 수 없다(요청: "대진 확정 버튼을 추가해주고 그걸 누르면 그때부터 시드는
        변경 못하게"). 확정 전까지는 부전승으로 이미 결정된 자리도 자유롭게 다시 배정할
        수 있다."""
        league = await self._get_or_404(league_id)
        if league.draw_size is None:
            raise ValidationError("아직 대진표가 없습니다.")
        if league.bracket_locked_at is None:
            league.bracket_locked_at = datetime.now(UTC)
            league.updated_by = actor.pk
            await self._session.commit()
            await self._session.refresh(league, attribute_names=["teams", "matches"])
        return to_league_out(league)

    async def set_match_slot(
        self, league_id: int, match_id: int, payload: LeagueMatchSlotIn, *, actor: Member,
    ) -> LeagueOut:
        league = await self._get_or_404(league_id)
        match = self._get_match_or_404(league, match_id)
        if league.bracket_locked_at is not None:
            raise ConflictError("대진이 확정돼 더 이상 시드를 바꿀 수 없습니다.")
        if match.sets_won_a is not None:
            raise ConflictError("이미 결과가 입력된 경기는 슬롯을 바꿀 수 없습니다.")
        if match.is_dead:
            raise ValidationError("이 자리는 구조적으로 비어있어(부전) 팀을 배정할 수 없습니다.")

        total_rounds = _total_rounds(league.draw_size) if league.draw_size else 0
        by_round_slot = {(m.round, m.slot_in_round): m for m in league.matches}

        # 이 자리가 부전승으로 이미 결정돼 있었다면(위에서 실제 결과는 걸러졌으니 여기
        # 남은 건 부전승뿐이다) 슬롯을 바꾸는 순간 그 결정 자체가 무효가 되므로, 전파된
        # 결과까지 포함해 먼저 취소한다.
        if match.winner_team_id is not None:
            self._undo_decided(match, by_round_slot, total_rounds, actor)

        team: LeagueTeam | None = None
        if payload.team_id is not None:
            team = self._get_team_or_404(league, payload.team_id)
            # 이미 이 라운드 다른 자리에 배정된 팀을 고르면 거부하지 않고 그 자리를
            # 비우고 옮긴다(요청: "이미 지정된 팀도 드롭다운에 나오고 새로 지정하면
            # 기존 지정된 슬롯을 미지정으로 지우는 식"). 그 자리가 실제 결과로 결정돼
            # 있었으면 거부하고, 부전승으로만 결정돼 있었으면 그 결정도 함께 취소한다.
            for m in league.matches:
                if m.id == match.id or m.round != match.round:
                    continue
                if m.team_a_id == team.id or m.team_b_id == team.id:
                    if m.sets_won_a is not None:
                        raise ConflictError(f"{team.label}팀은 이미 결과가 정해진 경기에 배정돼 있어 옮길 수 없습니다.")
                    if m.winner_team_id is not None:
                        self._undo_decided(m, by_round_slot, total_rounds, actor)
                    if m.team_a_id == team.id:
                        m.team_a_id = None
                    else:
                        m.team_b_id = None
                    m.updated_by = actor.pk

        if payload.side == "a":
            match.team_a_id = team.id if team else None
        else:
            match.team_b_id = team.id if team else None
        match.updated_by = actor.pk
        await self._repo.flush()

        # 팀을 배정한 경우에만 부전승 자동 처리를 확인한다(비우는 동작은 대상이 아니다).
        # 1라운드/2라운드 이상 판정 기준이 서로 달라 헬퍼를 나눠 쓴다(각 헬퍼 문서 참고).
        if team is not None:
            if match.round == 1:
                self._maybe_auto_resolve_round1(league, by_round_slot, total_rounds, match)
            else:
                self._maybe_auto_resolve(by_round_slot, total_rounds, match)

        await self._session.commit()
        await self._session.refresh(league, attribute_names=["teams", "matches"])
        await self._refresh_match_relations(league.matches)
        return to_league_out(league)

    async def set_bracket_seeding(
        self, league_id: int, payload: LeagueBracketSeedIn, *, actor: Member,
    ) -> LeagueOut:
        """1라운드 시드를 한 번에 저장한다(요청: "대진표 수정 시 그때그때 저장해서 느림 —
        화면만 수정하고 저장 버튼 누르면 그때 한 번에 저장"). set_match_slot을 자리마다
        호출하면 매번 서버 왕복+전체 리렌더가 생겨 느렸고, 두 팀 맞바꾸기처럼 순차 저장으론
        중간에 서로 덮어써(팀 이동 시 반대 자리를 자동으로 비우므로) 최종 상태가 깨지기도
        했다. 여기선 편집 가능한 1라운드 슬롯을 '전부 비운 뒤 다시 배정'해 순서 의존 없이
        원자적으로 반영하고, 부전승 자동 처리도 전부 배정한 뒤 한 번만 돌린다.

        payload.assignments는 편집 가능한 1라운드 슬롯 '전체'의 최종 배정을 담아야 한다 —
        빠진 자리는 비우는 것으로 간주된다(전부 비운 뒤 온 것만 다시 채우므로)."""
        league = await self._get_or_404(league_id)
        if league.bracket_locked_at is not None:
            raise ConflictError("대진이 확정돼 더 이상 시드를 바꿀 수 없습니다.")
        if league.draw_size is None:
            raise ValidationError("아직 대진표가 없습니다.")

        total_rounds = _total_rounds(league.draw_size)
        by_round_slot = {(m.round, m.slot_in_round): m for m in league.matches}

        # 편집 가능한 1라운드 자리 = 라운드1 & 부전 자리(is_dead) 아님 & 실제 결과 없음.
        editable = {
            m.id: m for m in league.matches
            if m.round == 1 and not m.is_dead and m.sets_won_a is None
        }

        # 들어온 배정을 (match_id, side) → team_id로 인덱싱하며 검증한다. 편집 불가 자리로
        # 온 배정은 거부하고, 한 팀이 두 자리에 오면 거부한다(1라운드엔 한 번만 등장해야 함).
        desired: dict[tuple[int, str], int | None] = {}
        seen_teams: set[int] = set()
        for a in payload.assignments:
            if a.match_id not in editable:
                raise ValidationError("이 자리는 시드를 바꿀 수 없습니다(부전·결과 입력됨·1라운드 아님).")
            if a.team_id is not None:
                self._get_team_or_404(league, a.team_id)  # 존재 검증
                if a.team_id in seen_teams:
                    raise ValidationError("한 팀을 두 자리에 배정할 수 없습니다.")
                seen_teams.add(a.team_id)
            desired[(a.match_id, a.side)] = a.team_id

        # 1) 편집 대상 자리의 기존 배정/부전승 결정을 모두 취소·비운다(전파된 결과까지 되돌림).
        for m in editable.values():
            if m.winner_team_id is not None:
                self._undo_decided(m, by_round_slot, total_rounds, actor)
            m.team_a_id = None
            m.team_b_id = None
            m.updated_by = actor.pk
        await self._repo.flush()

        # 2) 원하는 팀을 다시 배정한다(온 것만 채우고, 빠진 자리는 None으로 남긴다).
        for (match_id, side), team_id in desired.items():
            match = editable[match_id]
            if side == "a":
                match.team_a_id = team_id
            else:
                match.team_b_id = team_id
            match.updated_by = actor.pk
        await self._repo.flush()

        # 3) 부전승 자동 처리는 전부 배정한 뒤 한 번만 — 실제 팀이 배정된 자리만 대상.
        for m in editable.values():
            if m.team_a_id is not None or m.team_b_id is not None:
                self._maybe_auto_resolve_round1(league, by_round_slot, total_rounds, m)

        await self._session.commit()
        await self._session.refresh(league, attribute_names=["teams", "matches"])
        await self._refresh_match_relations(league.matches)
        return to_league_out(league)

    async def set_match_schedule(
        self, league_id: int, match_id: int, payload: LeagueMatchScheduleIn, *, actor: Member,
    ) -> LeagueMatchOut:
        league = await self._get_or_404(league_id)
        match = self._get_match_or_404(league, match_id)
        match.scheduled_at = payload.scheduled_at
        match.updated_by = actor.pk
        await self._session.commit()
        return to_match_out(match)

    async def enter_match_result(
        self, league_id: int, match_id: int, payload: LeagueMatchResultIn, *, actor: Member,
    ) -> LeagueOut:
        league = await self._get_or_404(league_id)
        match = self._get_match_or_404(league, match_id)
        if match.is_dead:
            raise ValidationError("성립하지 않는(부전) 경기입니다.")
        if match.winner_team_id is not None:
            raise ConflictError("이미 결과가 입력됐습니다.")
        if match.team_a_id is None or match.team_b_id is None:
            raise ValidationError("아직 양 팀이 모두 정해지지 않았습니다.")
        if league.mode == "individual" and payload.substitutes:
            raise ValidationError("개인리그는 대타를 지정할 수 없습니다.")

        threshold = league.best_of // 2 + 1
        a, b = payload.sets_won_a, payload.sets_won_b
        if a > league.best_of or b > league.best_of or a == b or max(a, b) != threshold:
            raise ValidationError("세트 스코어가 best_of와 맞지 않습니다.")

        team_by_id = {t.id: t for t in league.teams}
        subs_data: list[tuple[object, Member]] = []
        for sub in payload.substitutes:
            if sub.team_id not in (match.team_a_id, match.team_b_id):
                raise ValidationError("이 경기에 참가하지 않는 팀에는 대타를 지정할 수 없습니다.")
            team = team_by_id[sub.team_id]
            if sub.roster_position >= len(team.roster):
                raise ValidationError(f"{team.label}팀에 그 자리(로스터)가 없습니다.")
            m = await self._member_repo.get_by_login_id(sub.substitute_member_id)
            if m is None:
                raise NotFoundError(f"존재하지 않는 회원입니다: {sub.substitute_member_id}")
            subs_data.append((sub, m))

        match.sets_won_a = a
        match.sets_won_b = b
        match.winner_team_id = match.team_a_id if a > b else match.team_b_id
        match.result_entered_by = actor.pk
        match.result_entered_at = datetime.now(UTC)
        match.updated_by = actor.pk
        match.substitutions = [
            LeagueMatchSubstitution(
                league_match_id=match.id, team_id=sub.team_id,
                roster_position=sub.roster_position, substitute_member_pk=m.pk, note=sub.note,
            )
            for sub, m in subs_data
        ]
        await self._repo.flush()

        # 다음 라운드로 승자를 전파한다 — generate_bracket의 부전승 자동 처리와 정확히 같은
        # 로직을 재사용한다. 반대쪽 자리가 이미 영원히 안 채워지는 상태(형제 슬롯이
        # is_dead)라면, 이 실제 경기 결과가 들어오는 즉시 다음 라운드도 자동으로 부전승
        # 처리된다(요청 없이 admin이 "부전승 결과"를 따로 입력할 필요가 없다).
        total_rounds = _total_rounds(league.draw_size)
        by_round_slot = {(m.round, m.slot_in_round): m for m in league.matches}
        self._propagate_winner(by_round_slot, total_rounds, match.round, match.slot_in_round, match.winner_team_id)

        await self._session.commit()
        await self._session.refresh(league, attribute_names=["teams", "matches"])
        await self._refresh_match_relations(league.matches)
        return to_league_out(league)

    async def clear_match_result(self, league_id: int, match_id: int, *, actor: Member) -> LeagueOut:
        league = await self._get_or_404(league_id)
        match = self._get_match_or_404(league, match_id)
        if match.sets_won_a is None:
            raise ValidationError("취소할 결과가 없습니다(부전승은 취소할 수 없습니다).")

        total_rounds = _total_rounds(league.draw_size)
        by_round_slot = {(m.round, m.slot_in_round): m for m in league.matches}
        self._undo_decided(match, by_round_slot, total_rounds, actor)

        await self._session.commit()
        await self._session.refresh(league, attribute_names=["teams", "matches"])
        await self._refresh_match_relations(league.matches)
        return to_league_out(league)
