from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.domain.leagues.models import League, LeagueMatch, LeagueMatchSubstitution, LeagueTeam, LeagueTeamMember
from app.domain.leagues.repository import LeagueRepository
from app.domain.leagues.schemas import (
    LeagueCreateIn,
    LeagueListItemOut,
    LeagueListOut,
    LeagueMatchOut,
    LeagueMatchResultIn,
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

TEAM_LABELS = "ABCDEF"
MAX_TEAMS = 6


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
        status=_status_of(league), drawSize=league.draw_size,
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
        if league.draw_size is not None:
            raise ValidationError("대진표 생성 후에는 팀을 추가할 수 없습니다.")
        if len(league.teams) >= MAX_TEAMS:
            raise ValidationError("팀은 최대 6개까지 만들 수 있습니다.")
        team = LeagueTeam(
            league_id=league.id, label=TEAM_LABELS[len(league.teams)],
            created_by=actor.pk, updated_by=actor.pk,
        )
        league.teams.append(team)
        await self._repo.flush()
        await self._session.commit()
        await self._session.refresh(team, attribute_names=["roster"])
        return to_team_out(team)

    async def delete_team(self, league_id: int, team_id: int, *, actor: Member) -> LeagueOut:
        league = await self._get_or_404(league_id)
        if league.draw_size is not None:
            raise ValidationError("대진표 생성 후에는 팀을 삭제할 수 없습니다.")
        team = self._get_team_or_404(league, team_id)
        league.teams.remove(team)
        await self._session.flush()
        # 라벨 재정렬 — 한 팀씩 순서대로 flush해 UniqueConstraint(league_id, label)와
        # 일시적으로 충돌하지 않게 한다(예: C→B로 옮길 때 기존 B가 먼저 비워져 있어야 함).
        remaining = sorted(league.teams, key=lambda t: t.label)
        for i, t in enumerate(remaining):
            new_label = TEAM_LABELS[i]
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
        if league.draw_size is not None:
            raise ValidationError("대진표 생성 후에는 로스터를 바꿀 수 없습니다.")
        team = self._get_team_or_404(league, team_id)
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

    async def generate_bracket(self, league_id: int, *, actor: Member) -> LeagueOut:
        league = await self._get_or_404(league_id)
        if league.draw_size is not None:
            raise ValidationError("이미 대진표가 생성됐습니다.")
        teams = sorted(league.teams, key=lambda t: t.label)
        if len(teams) < 2:
            raise ValidationError("최소 2팀이 있어야 대진표를 만들 수 있습니다.")

        draw_size = _next_pow2(len(teams))
        total_rounds = _total_rounds(draw_size)
        league.draw_size = draw_size
        league.updated_by = actor.pk

        positions: list[LeagueTeam | None] = list(teams) + [None] * (draw_size - len(teams))

        # is_dead(그 슬롯이 구조적으로 영원히 안 채워짐)를 리프(포지션)부터 전체 라운드에
        # 걸쳐 미리 계산해둔다. 이걸 생성 시점에 한 라운드씩(runtime resolve 중에) "양쪽 다
        # None이면 dead"로 즉석 판정하면, 아직 실제 경기 결과를 기다리는 중이라 None인 것과
        # 구분이 안 된다 — 실제로 3팀(부전승 팀이 다음 라운드에서 실제 상대 없이 결승까지
        # 자동 진출해버리는) 경우에서 이 오판이 발생함을 확인했다. 리프에서부터 "이 슬롯
        # 아래 서브트리에 팀이 하나도 없다"만을 순수하게 각 라운드마다 접어 올려 계산하면,
        # "아직 안 끝난 실제 경기"와 "영원히 안 채워짐"이 항상 명확히 구분된다.
        dead: dict[int, list[bool]] = {
            1: [
                positions[2 * s] is None and positions[2 * s + 1] is None
                for s in range(draw_size // 2)
            ],
        }
        for r in range(2, total_rounds + 1):
            dead[r] = [dead[r - 1][2 * s] and dead[r - 1][2 * s + 1] for s in range(draw_size // (2 ** r))]

        by_round_slot: dict[tuple[int, int], LeagueMatch] = {}
        for r in range(1, total_rounds + 1):
            count = draw_size // (2 ** r)
            for slot in range(count):
                if r == 1:
                    a, b = positions[2 * slot], positions[2 * slot + 1]
                    m = LeagueMatch(
                        league_id=league.id, round=r, slot_in_round=slot,
                        team_a_id=a.id if a else None, team_b_id=b.id if b else None,
                        is_dead=dead[1][slot],
                        created_by=actor.pk, updated_by=actor.pk,
                    )
                else:
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

        for slot in range(draw_size // 2):
            self._maybe_auto_resolve(by_round_slot, total_rounds, by_round_slot[(1, slot)])

        await self._session.commit()
        await self._session.refresh(league, attribute_names=["teams", "matches"])
        # 부전승 전파(_maybe_auto_resolve)가 team_a_id/team_b_id를 관계 속성이 아니라
        # 원시 FK 컬럼으로 직접 바꿔서, 그 대상이 된 매치들의 team_a/team_b가 여전히
        # 예전 값(비어있음)으로 캐시돼 있을 수 있다 — 응답 직렬화 전에 다시 새로고침한다.
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

    def _maybe_auto_resolve(
        self, by_round_slot: dict[tuple[int, int], LeagueMatch], total_rounds: int, match: LeagueMatch,
    ) -> None:
        """반대쪽 자리가 영원히 안 채워지는 상태에서 한쪽만 채워지면 자동으로 부전승
        처리하고 다음 라운드로 전파한다.

        1라운드는 team_a_id/team_b_id가 생성 시점에 확정돼 다시는 안 바뀌므로, 한쪽만
        있으면 그건 무조건 영구 공백(대진판이 2의 거듭제곱이 아니라 생기는 패딩)이다.
        하지만 2라운드 이상에서는 "비어있음"이 두 가지 뜻일 수 있다 — ①(그 자리를 먹이는
        이전 라운드 경기 자체가 is_dead라서) 영원히 안 채워지거나, ②아직 그 이전 라운드의
        실제 경기 결과를 기다리는 중이거나. ②인데 ①처럼 자동 부전승 처리해버리면, 부전승
        팀이 다음 라운드에서 실제 상대와 붙어야 하는데도 그걸 건너뛰고 계속 자동
        진출해버리는 버그가 생긴다(실제로 발생 확인 — 3팀 대진표에서 부전승 팀이 결승
        상대 없이 바로 우승 처리됨). 그래서 2라운드 이상에서는 비어있는 쪽을 먹이는
        이전 라운드 경기의 is_dead를 직접 확인해, ①일 때만 자동 처리한다."""
        if match.is_dead or match.winner_team_id is not None:
            return
        a, b = match.team_a_id, match.team_b_id
        if a is not None and b is not None:
            return  # 양쪽 다 실제 팀 — 진짜 경기를 치러야 함, 자동 처리 대상 아님
        if a is None and b is None:
            return  # 둘 다 아직 None — is_dead가 아니므로 언젠가 실제 경기로 채워질 예정
        if match.round > 1:
            empty_child_slot = match.slot_in_round * 2 + (1 if a is not None else 0)
            feeder = by_round_slot.get((match.round - 1, empty_child_slot))
            if feeder is None or not feeder.is_dead:
                return  # 아직 실제 경기(위 ②) 결과를 기다리는 중 — 자동 처리하지 않는다
        winner = a if a is not None else b
        match.winner_team_id = winner
        match.result_entered_at = datetime.now(UTC)
        self._propagate_winner(by_round_slot, total_rounds, match.round, match.slot_in_round, winner)

    async def set_match_slot(
        self, league_id: int, match_id: int, payload: LeagueMatchSlotIn, *, actor: Member,
    ) -> LeagueMatchOut:
        league = await self._get_or_404(league_id)
        match = self._get_match_or_404(league, match_id)
        if match.winner_team_id is not None:
            raise ConflictError("이미 결과가 입력된 경기는 슬롯을 바꿀 수 없습니다.")

        team: LeagueTeam | None = None
        if payload.team_id is not None:
            team = self._get_team_or_404(league, payload.team_id)
            for m in league.matches:
                if m.id == match.id or m.round != match.round:
                    continue
                if m.team_a_id == team.id or m.team_b_id == team.id:
                    raise ConflictError(f"{team.label}팀이 이미 이 라운드의 다른 자리에 배정돼 있습니다.")

        if payload.side == "a":
            match.team_a_id = team.id if team else None
        else:
            match.team_b_id = team.id if team else None
        match.is_dead = False
        match.updated_by = actor.pk
        await self._session.commit()
        await self._session.refresh(match, attribute_names=["team_a", "team_b"])
        return to_match_out(match)

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

        def clear(m: LeagueMatch) -> None:
            m.winner_team_id = None
            m.sets_won_a = None
            m.sets_won_b = None
            m.result_entered_by = None
            m.result_entered_at = None
            m.substitutions = []
            m.updated_by = actor.pk
            if m.round < total_rounds:
                next_round, next_slot = m.round + 1, m.slot_in_round // 2
                side = "team_a_id" if m.slot_in_round % 2 == 0 else "team_b_id"
                target = by_round_slot.get((next_round, next_slot))
                if target is not None:
                    setattr(target, side, None)
                    if target.winner_team_id is not None:
                        clear(target)

        clear(match)
        await self._session.commit()
        await self._session.refresh(league, attribute_names=["teams", "matches"])
        await self._refresh_match_relations(league.matches)
        return to_league_out(league)
