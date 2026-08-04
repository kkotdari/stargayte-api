import string
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.domain.leagues.models import (
    League,
    LeagueMatch,
    LeagueMatchSubstitution,
    LeagueTeam,
    LeagueTeamMember,
)
from app.domain.leagues.repository import LeagueRepository
from app.domain.leagues.schemas import (
    LeagueBracketGenerateIn,
    LeagueBracketSeedIn,
    LeagueCreateIn,
    LeagueListItemOut,
    LeagueListOut,
    LeagueMatchOut,
    LeagueMatchSubstitutionOut,
    LeagueMatchTeamRefOut,
    LeagueOut,
    LeagueRosterMemberOut,
    LeagueTeamCompositionEntry,
    LeagueTeamCompositionIn,
    LeagueTeamOut,
)
from app.domain.members.models import Member
from app.domain.members.repository import MemberRepository


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

    async def _refresh_match_relations(self, matches: list[LeagueMatch]) -> None:
        """team_a_id/team_b_id를 (관계 속성이 아니라) 원시 FK 컬럼으로 직접 바꾸는 곳들
        (_propagate_winner의 부전승 전파)이 있어, 그 즉시 team_a/team_b
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

    async def delete_league(self, league_id: int) -> None:
        league = await self._get_or_404(league_id)
        await self._session.delete(league)
        await self._session.commit()

    async def set_team_composition(
        self, league_id: int, payload: LeagueTeamCompositionIn, *, actor: Member,
    ) -> LeagueOut:
        """리그의 팀/선수 구성을 한 번에 저장한다(요청: "팀구성 따로 배치 저장"). teams는
        원하는 '전체' 구성(순서=라벨 순서)이다 — 기존 팀은 로스터만 갱신, 빠진 팀은 삭제,
        id=None은 새로 만들고, 라벨을 순서대로 다시 매긴다. add_team/delete_team/set_roster를
        자리마다 부르던 방식(매번 서버 왕복+리로드)을 대체하며, 멤버를 팀 사이로 옮기는
        편집도 (league_id, member_pk) 유니크 충돌 없이 원자적으로 반영한다. 저장 뒤 프론트가
        리그를 다시 불러와 대진표도 새 팀 구성으로 갱신한다."""
        league = await self._get_or_404(league_id)

        # 대진표가 이미 있으면 예약된 자리(planned_teams)를 넘는 팀은 담을 수 없다(add_team과
        # 같은 규칙 — 대진판에 자리가 없다).
        if league.draw_size is not None and len(payload.teams) > (league.planned_teams or 0):
            raise ValidationError("이 대진표에 예약된 자리보다 많은 팀을 둘 수 없습니다.")

        existing_by_id = {t.id: t for t in league.teams}
        payload_ids = [e.id for e in payload.teams if e.id is not None]
        if len(set(payload_ids)) != len(payload_ids):
            raise ValidationError("같은 팀이 중복으로 들어왔습니다.")
        for tid in payload_ids:
            if tid not in existing_by_id:
                raise NotFoundError("존재하지 않는 팀입니다.")

        # 로스터 회원 로드 + 검증(개인전 인원수, 회원 존재, 팀 간 중복). resolved에 팀 순서대로
        # (엔트리, 회원목록)을 쌓아 이후 반영에 그대로 쓴다.
        seen_member_pks: set[int] = set()
        resolved: list[tuple[LeagueTeamCompositionEntry, list[Member]]] = []
        for entry in payload.teams:
            if league.mode == "individual" and len(entry.roster) > 1:
                raise ValidationError("개인리그는 선수를 1명으로만 구성할 수 있습니다.")
            members: list[Member] = []
            for member_id in entry.roster:
                m = await self._member_repo.get_by_login_id(member_id)
                if m is None:
                    raise NotFoundError(f"존재하지 않는 회원입니다: {member_id}")
                if m.pk in seen_member_pks:
                    raise ConflictError(f"한 회원이 두 팀에 들어갈 수 없습니다: {m.nickname}")
                seen_member_pks.add(m.pk)
                members.append(m)
            resolved.append((entry, members))

        # 이미 결과가 난 팀은 삭제·로스터 변경을 막는다(set_roster/delete_team과 같은 원칙).
        for t in league.teams:
            if not self._team_has_decided_match(league, t.id):
                continue
            entry = next((e for e in payload.teams if e.id == t.id), None)
            if entry is None:
                raise ValidationError(f"{t.label}팀은 이미 결과가 나온 경기에 참가해 삭제할 수 없습니다.")
            old_roster = [ltm.member.id for ltm in sorted(t.roster, key=lambda x: x.position)]
            if old_roster != list(entry.roster):
                raise ValidationError(f"{t.label}팀은 이미 결과가 나온 경기에 참가해 로스터를 바꿀 수 없습니다.")

        # 1) payload에 없는 기존 팀 삭제(대진 슬롯에 배정돼 있었다면 FK ON DELETE SET NULL이
        #    그 자리를 자동으로 비운다).
        keep_ids = set(payload_ids)
        for t in list(league.teams):
            if t.id not in keep_ids:
                league.teams.remove(t)
        await self._session.flush()

        # 2) 남은 팀 로스터를 전부 비운다 — 멤버 이동 시 (league_id, member_pk) 유니크가
        #    delete/insert 순서로 일시 충돌하는 걸 막으려면 먼저 다 지우고 flush해야 한다.
        for t in league.teams:
            for ltm in list(t.roster):
                await self._session.delete(ltm)
        await self._session.flush()

        # 3) 새 팀 생성(임시 라벨) + 최종 순서 리스트 구성. 임시 라벨 "#i"는 문자 라벨(A,B..)과
        #    절대 겹치지 않아 유니크(league_id,label) 충돌이 없다.
        final: list[tuple[LeagueTeam, list[Member]]] = []
        for i, (entry, members) in enumerate(resolved):
            if entry.id is not None:
                team = existing_by_id[entry.id]
            else:
                team = LeagueTeam(
                    league_id=league.id, label=f"#{i}", created_by=actor.pk, updated_by=actor.pk,
                )
                league.teams.append(team)
            final.append((team, members))
        await self._session.flush()

        # 4) 라벨 2단계 재부여: 먼저 전부 임시("#i")로 옮겨 유니크 충돌을 피하고, 그다음 최종
        #    문자 라벨로. (팀 순서를 바꾸는 편집도 안전.)
        for i, (team, _members) in enumerate(final):
            team.label = f"#{i}"
            team.updated_by = actor.pk
        await self._session.flush()
        for i, (team, _members) in enumerate(final):
            team.label = _team_label(i)
        await self._session.flush()

        # 5) 로스터 삽입. team.roster 컬렉션에 대입하면 delete-orphan 비교를 위해 (아직 로드된
        #    적 없는 새 팀의) 컬렉션을 지연 로드하려다 async 밖에서 MissingGreenlet이 난다 —
        #    이미 step 2에서 남은 팀 로스터를 다 지웠고 새 팀은 비어 있으니, 관계 대입 대신
        #    행을 세션에 직접 추가한다(FK로 팀에 연결, 컬렉션은 마지막 refresh에서 갱신).
        for team, members in final:
            for j, m in enumerate(members):
                self._session.add(
                    LeagueTeamMember(
                        league_id=league.id, league_team_id=team.id, member_pk=m.pk, position=j,
                    )
                )
        await self._session.commit()
        await self._session.refresh(league, attribute_names=["teams", "matches"])
        # 새로 만든 팀은 refresh(league,["teams"])만으론 roster가 eager 로드되지 않아,
        # 직렬화(to_team_out) 때 async 밖에서 지연 로드되며 MissingGreenlet이 난다 —
        # set_roster처럼 팀별 roster를 명시적으로 새로고침한다(roster의 member는 selectin).
        for t in league.teams:
            await self._session.refresh(t, attribute_names=["roster"])
        await self._refresh_match_relations(league.matches)
        return to_league_out(league)

    async def generate_bracket(
        self, league_id: int, payload: LeagueBracketGenerateIn, *, actor: Member,
    ) -> LeagueOut:
        """빈 대진표를 만든다 — 크기는 라운드 수로 직접 받는다(요청).

        예전에는 팀 수에서 다음 2의 거듭제곱을 골라 판을 잡고, 모자란 자리를 부전승으로
        메웠다. 이제는 어느 라운드의 칸에나 팀을 앉힐 수 있어서(set_bracket_seeding) 그
        계산이 성립하지 않는다 — 여덟 칸짜리 판에 여섯을 앉히든 셋을 앉히든 관리자 마음이고,
        아무도 안 앉은 가지는 확정할 때 사라진다(confirm_bracket). 부전승은 그 결과로
        저절로 생긴다: 한 사람만 있는 가지는 그대로 다음 라운드로 올라간다.
        """
        league = await self._get_or_404(league_id)
        if league.bracket_locked_at is not None:
            raise ConflictError("대진이 확정돼 규모를 바꿀 수 없습니다.")
        rounds = payload.rounds
        draw_size = 2 ** rounds

        # 이미 결과가 들어간 경기가 있으면 판을 다시 잡을 수 없다 — 재생성이 그 진행 상황을
        # 지운다. 결과가 하나도 없으면 배정만 남으므로, 새 판에도 있는 칸은 그대로 살린다
        # (요청: "참가팀수 늘릴 때 기존 지정된 건 리셋하지 말아줘").
        old_by_key: dict[tuple[int, int], LeagueMatch] = {}
        if league.draw_size is not None:
            if any(m.winner_team_id is not None for m in league.matches):
                raise ValidationError("이미 결과가 입력된 경기가 있어 대진표 규모를 바꿀 수 없습니다.")
            for m in list(league.matches):
                old_by_key[(m.round, m.slot_in_round)] = m
            await self._repo.flush()

        league.draw_size = draw_size
        # 이 판이 담을 수 있는 최대 인원 — 팀구성 저장이 이 값으로 상한을 건다.
        league.planned_teams = draw_size
        league.updated_by = actor.pk

        by_round_slot: dict[tuple[int, int], LeagueMatch] = {}
        for r in range(1, rounds + 1):
            for slot in range(draw_size // (2 ** r)):
                m = old_by_key.pop((r, slot), None)
                if m is not None:
                    # 확정 전에는 죽은 칸이 없다 — 어느 가지가 살아남을지는 확정 때 정해진다.
                    m.is_dead = False
                    m.updated_by = actor.pk
                else:
                    m = LeagueMatch(
                        league_id=league.id, round=r, slot_in_round=slot, is_dead=False,
                        created_by=actor.pk, updated_by=actor.pk,
                    )
                    league.matches.append(m)
                by_round_slot[(r, slot)] = m
        # 판이 줄어 더는 없는 칸은 배정째로 버려진다.
        for leftover in old_by_key.values():
            league.matches.remove(leftover)
        await self._repo.flush()
        # 방금 만든 LeagueMatch는 관계가 아직 로드된 적이 없어(새 객체라 selectin이 안 걸린다)
        # to_match_out에서 지연 로딩이 걸리면 MissingGreenlet이 난다 — 미리 채워 둔다.
        await self._refresh_match_relations(list(by_round_slot.values()))

        await self._session.commit()
        await self._session.refresh(league, attribute_names=["teams", "matches"])
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
        처리하고 다음 라운드로 전파한다. 2라운드 이상 전용이다(1라운드는 위
        확정 때 한 번에 정리된다 — _prune_and_resolve 참고).

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

    def _prune_and_resolve(self, league: League, actor: Member) -> None:
        """확정 순간에 대진 모양을 굳힌다(요청) — 아무도 안 앉은 가지를 죽이고, 한 사람만
        있는 가지를 그대로 올려 보낸다(그게 곧 부전승이다).

        예전에는 "부전승 자리"를 따로 찍어 두고 1라운드만 특별 취급했다. 이제는 어느 칸에나
        팀을 앉힐 수 있어서(set_bracket_seeding) 그럴 필요가 없다: 3라운드 칸에 바로 앉은
        팀이 있으면 그 아래 1·2라운드는 아무도 안 앉은 가지라 통째로 죽고, 그 팀은 3라운드
        부터 경기를 한다. 부전승은 이 규칙의 결과일 뿐 따로 다루는 개념이 아니다.

        지우지 않고 표시만 한다(요청) — 원본을 남겨 두면 나중에 확정을 푸는 길이 열린다.
        """
        draw_size = league.draw_size or 0
        total_rounds = _total_rounds(draw_size)
        by = {(m.round, m.slot_in_round): m for m in league.matches}

        # 1) 씨앗이 하나라도 있는 가지만 산다 — 아래에서 위로 훑으면 한 번에 끝난다.
        live: dict[tuple[int, int], bool] = {}
        for r in range(1, total_rounds + 1):
            for slot in range(draw_size // (2 ** r)):
                m = by.get((r, slot))
                if m is None:
                    continue
                seeded = m.team_a_id is not None or m.team_b_id is not None
                below = r > 1 and (live.get((r - 1, 2 * slot), False) or live.get((r - 1, 2 * slot + 1), False))
                live[(r, slot)] = seeded or below
        for key, m in by.items():
            m.is_dead = not live.get(key, False)
            m.updated_by = actor.pk

        # 2) 아래에서 위로 진출을 태운다 — 한쪽만 찬 칸의 반대쪽이 영영 안 채워지면 그대로
        #    올라간다. 1라운드는 아래가 없으니 그 자체로 확정이고, 그 위는 먹여 주는 경기가
        #    죽었는지로 가른다.
        for r in range(1, total_rounds + 1):
            for slot in range(draw_size // (2 ** r)):
                m = by.get((r, slot))
                if m is None or m.is_dead or m.winner_team_id is not None:
                    continue
                a, b = m.team_a_id, m.team_b_id
                if (a is None) == (b is None):
                    continue  # 둘 다 찼거나 둘 다 비었다 — 여기서 정할 게 없다
                if r > 1:
                    feeder = by.get((r - 1, 2 * slot + (1 if a is not None else 0)))
                    if feeder is not None and not feeder.is_dead:
                        continue  # 아직 올라올 사람이 있다
                winner = a if a is not None else b
                m.winner_team_id = winner
                m.result_entered_at = datetime.now(UTC)
                self._propagate_winner(by, total_rounds, r, slot, winner)

    async def confirm_bracket(self, league_id: int, *, actor: Member) -> LeagueOut:
        """대진을 확정한다 — 그 뒤로는 시드를 더는 바꿀 수 없다(요청).

        확정은 잠그기만 하는 것이 아니라 대진 모양을 굳히는 순간이기도 하다(요청: 확정을
        누르면 필요 없는 칸이 사라진다) — 아무도 안 앉은 가지를 죽이고, 한 사람만 있는
        가지를 다음 라운드로 올린다(_prune_and_resolve).
        """
        league = await self._get_or_404(league_id)
        if league.draw_size is None:
            raise ValidationError("아직 대진표가 없습니다.")
        if league.bracket_locked_at is None:
            if not any(
                m.team_a_id is not None or m.team_b_id is not None for m in league.matches
            ):
                raise ValidationError("아직 아무도 배정되지 않았습니다.")
            self._prune_and_resolve(league, actor)
            league.bracket_locked_at = datetime.now(UTC)
            league.updated_by = actor.pk
            await self._session.commit()
            await self._session.refresh(league, attribute_names=["teams", "matches"])
            await self._refresh_match_relations(league.matches)
        return to_league_out(league)

    async def set_bracket_seeding(
        self, league_id: int, payload: LeagueBracketSeedIn, *, actor: Member,
    ) -> LeagueOut:
        """대진표의 팀 배정을 한 번에 저장한다(요청: 화면에서 다 고친 뒤 저장 버튼으로).

        1라운드뿐 아니라 **어느 라운드의 칸에나** 앉힐 수 있다(요청) — 3라운드부터 붙는
        가지를 만들면 그 아래는 확정할 때 사라진다. 그래서 "한쪽은 토너먼트, 다른 쪽은
        단판, 그 둘이 결승" 같은 복합 대진이 부전승을 따로 찍지 않고 그냥 나온다.

        assignments는 편집 가능한 칸 '전체'의 최종 배정을 담는다 — 서버가 먼저 모두 비운 뒤
        다시 배정해, 두 팀 맞바꾸기 같은 편집도 순서에 안 흔들린다.
        """
        league = await self._get_or_404(league_id)
        if league.bracket_locked_at is not None:
            raise ConflictError("대진이 확정돼 더 이상 시드를 바꿀 수 없습니다.")
        if league.draw_size is None:
            raise ValidationError("아직 대진표가 없습니다.")

        total_rounds = _total_rounds(league.draw_size)
        by_round_slot = {(m.round, m.slot_in_round): m for m in league.matches}
        editable = {m.id: m for m in league.matches if m.sets_won_a is None}

        desired: dict[tuple[int, str], int | None] = {}
        seen_teams: set[int] = set()
        for a in payload.assignments:
            if a.match_id not in editable:
                raise ValidationError("이 자리는 시드를 바꿀 수 없습니다(결과가 입력된 경기).")
            if a.team_id is not None:
                self._get_team_or_404(league, a.team_id)  # 존재 검증
                if a.team_id in seen_teams:
                    raise ValidationError("한 팀을 두 자리에 배정할 수 없습니다.")
                seen_teams.add(a.team_id)
            desired[(a.match_id, a.side)] = a.team_id

        for m in editable.values():
            if m.winner_team_id is not None:
                self._undo_decided(m, by_round_slot, total_rounds, actor)
            m.team_a_id = None
            m.team_b_id = None
            m.updated_by = actor.pk
        await self._repo.flush()

        for (match_id, side), team_id in desired.items():
            match = editable[match_id]
            if side == "a":
                match.team_a_id = team_id
            else:
                match.team_b_id = team_id
            match.updated_by = actor.pk

        # 같은 가지에 씨앗이 둘이면 누가 올라갈지 모순이다(요청: 거부) — 3라운드 칸에
        # 앉은 팀이 있는데 그 자리로 올라올 아래 경기에도 팀이 있으면, 그 승자가 갈 자리가
        # 이미 차 있다. 배정을 다 한 뒤 한 번에 본다: 하나씩 볼 때는 어느 쪽이 나중에
        # 올지 몰라 순서에 따라 판정이 달라진다.
        #
        # 쪽(side)을 반드시 따져야 한다 — a자리는 아래 짝수 슬롯이, b자리는 홀수 슬롯이
        # 먹인다. 칸 단위로만 보면 "결승 b자리에 앉은 팀"이 반대편(a쪽) 가지의 배정과
        # 부딪친 것으로 잘못 걸린다(실측).
        seeded: set[tuple[int, int]] = set()
        for m in league.matches:
            if m.team_a_id is not None:
                seeded.add((m.round, m.slot_in_round * 2))       # a쪽 = 아래 짝수 슬롯 자리
            if m.team_b_id is not None:
                seeded.add((m.round, m.slot_in_round * 2 + 1))   # b쪽 = 아래 홀수 슬롯 자리
        for r, half in sorted(seeded, reverse=True):
            below = [(r - 1, half)]
            while below:
                rr, ss = below.pop()
                if rr < 1:
                    continue
                if (rr, ss * 2) in seeded or (rr, ss * 2 + 1) in seeded:
                    raise ValidationError(
                        "이 자리로 올라올 아래 경기에 이미 팀이 있습니다. 한 가지에는 한 곳만 배정하세요.",
                    )
                below += [(rr - 1, ss * 2), (rr - 1, ss * 2 + 1)]
        await self._repo.flush()

        await self._session.commit()
        await self._session.refresh(league, attribute_names=["teams", "matches"])
        await self._refresh_match_relations(league.matches)
        return to_league_out(league)
