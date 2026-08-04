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
    LeagueBracketByesIn,
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
        isDead=match.is_dead, byeSide=match.bye_side, scheduledAt=match.scheduled_at,
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
        self._default_byes(by_round_slot, draw_size, team_count, actor)
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

    def _default_byes(
        self, by_round_slot: dict[tuple[int, int], LeagueMatch],
        draw_size: int, team_count: int, actor: Member,
    ) -> None:
        """대진표를 만들 때 부전승 자리를 기본값으로 깐다 — 관리자가 나중에 옮긴다(요청).

        자리는 앞쪽부터 고르되 '이미 두 팀이 다 찬 칸'은 건너뛴다. 규모를 다시 잡아도 이미
        해 둔 1라운드 배정은 그대로 둔다는 약속이 있어서다(요청: "참가팀수 늘릴 때 기존
        지정된 건 리셋하지 말아줘") — 다 찬 칸에 부전승을 얹으면 그 약속을 깨고 한 팀을
        쫓아내야 한다. 빈 자리 쪽에 얹고, 양쪽 다 비었으면 b쪽이다(a에 실제 팀이 선다).

        비켜 갈 자리가 모자라면(모든 칸이 다 차 있는데 부전승이 더 필요한 경우) 앞쪽 칸을
        쓰고 그 자리의 b팀을 비운다 — 부전승 개수가 안 맞는 대진표를 남기는 것보다는 낫다.
        """
        need = draw_size - team_count
        slots = [by_round_slot[(1, s)] for s in range(draw_size // 2)]
        for m in slots:
            m.bye_side = None
        if need <= 0:
            return
        free = [m for m in slots if m.team_a_id is None or m.team_b_id is None]
        picked = free[:need]
        if len(picked) < need:
            picked += [m for m in slots if m not in picked][: need - len(picked)]
        for m in picked:
            if m.team_a_id is None:
                m.bye_side = "a" if m.team_b_id is not None else "b"
            else:
                m.bye_side = "b"
                m.team_b_id = None  # 다 찬 칸을 어쩔 수 없이 쓴 경우
            m.updated_by = actor.pk

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
        """1라운드 전용 부전승 자동 처리 — 실제 팀이 슬롯에 배정되는 순간 호출된다.
        어느 칸의 어느 쪽이 부전승인지는 칸에 적혀 있다(match.bye_side) — 예전에는
        "앞쪽 byes개 슬롯"이라고 코드가 정했는데, 그러면 대진 모양이 하나로 고정돼
        "한쪽은 토너먼트, 다른 쪽은 단판" 같은 구조를 만들 수 없었다(요청).

        부전승 자리가 아니면(양쪽 다 실제 경기가 필요한 자리) 한쪽만 채워졌어도 반대쪽
        실제 팀 배정을 기다린다."""
        del league  # 이제 리그 전체를 안 봐도 된다 — 판정에 필요한 건 칸에 다 적혀 있다.
        if match.is_dead or match.winner_team_id is not None:
            return
        a, b = match.team_a_id, match.team_b_id
        if a is not None and b is not None:
            return
        if a is None and b is None:
            return
        # 부전승 자리의 '반대쪽'에 팀이 서야 그 팀이 올라간다 — 빈 자리 쪽에 팀을 놓는 건
        # 애초에 막지만(set_bracket_seeding), 여기서도 잘못된 조합에는 손대지 않는다.
        winner = None
        if match.bye_side == "b":
            winner = a
        elif match.bye_side == "a":
            winner = b
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

    async def set_bracket_byes(
        self, league_id: int, payload: LeagueBracketByesIn, *, actor: Member,
    ) -> LeagueOut:
        """부전승 자리를 관리자가 고른다(요청).

        같은 8강 대진이라도 부전승을 어디에 두느냐로 대진 모양이 달라진다 — 앞쪽 두 칸에
        두면 "두 팀이 4강 직행 + 네 팀이 8강"이고, 뒤쪽 두 칸에 두면 "네 팀 토너먼트 +
        두 팀 단판, 그 둘이 결승"이다. 후자가 요청받은 구조이고, 그건 자료구조를 바꾸지
        않고 부전승 자리만 옮기면 나오는 모양이다.

        slots는 '전체' 부전승 자리를 담는다 — 기존 배치를 모두 지우고 이 목록대로 다시
        깐다(시드 저장과 같은 방식). 자리가 바뀌면 그 자리에서 이미 올라갔던 부전승 결정도
        함께 되돌린다: 안 그러면 부전승이 아니게 된 칸의 팀이 그대로 다음 라운드에 남는다.
        """
        league = await self._get_or_404(league_id)
        if league.bracket_locked_at is not None:
            raise ConflictError("대진이 확정돼 부전승 자리를 바꿀 수 없습니다.")
        if league.draw_size is None:
            raise ValidationError("아직 대진표가 없습니다.")

        need = league.draw_size - (league.planned_teams or 0)
        if len(payload.slots) != need:
            raise ValidationError(f"부전승 자리는 정확히 {need}개여야 합니다.")

        total_rounds = _total_rounds(league.draw_size)
        by_round_slot = {(m.round, m.slot_in_round): m for m in league.matches}
        round1 = {m.id: m for m in league.matches if m.round == 1 and not m.is_dead}

        # 실제로 치른 경기가 하나라도 있으면 대진 모양 자체를 바꿀 수 없다 — 부전승 자리를
        # 옮기는 건 "누가 누구와 붙나"를 다시 짜는 일이라, 이미 붙은 판이 있으면 앞뒤가 안 맞다.
        if any(m.sets_won_a is not None for m in round1.values()):
            raise ValidationError("이미 결과가 입력된 경기가 있어 부전승 자리를 바꿀 수 없습니다.")

        wanted: dict[int, str] = {}
        for slot in payload.slots:
            if slot.match_id not in round1:
                raise ValidationError("부전승은 1라운드 자리에만 둘 수 있습니다.")
            if slot.match_id in wanted:
                raise ValidationError("한 경기에 부전승을 둘 이상 둘 수 없습니다.")
            wanted[slot.match_id] = slot.side

        # 1) 부전승으로 이미 올라간 결정을 되돌린다(전파된 다음 라운드까지 함께).
        for m in round1.values():
            if m.winner_team_id is not None:
                self._undo_decided(m, by_round_slot, total_rounds, actor)

        # 2) 새 배치를 깐다. 부전승이 된 자리에 팀이 서 있으면 반대쪽으로 옮기고(반대쪽이
        #    비어 있을 때만), 옮길 데가 없으면 비운다 — 영구 공백에 선 팀은 영원히 안 붙는다.
        for m in round1.values():
            side = wanted.get(m.id)
            m.bye_side = side
            if side == "b" and m.team_b_id is not None:
                if m.team_a_id is None:
                    m.team_a_id = m.team_b_id
                m.team_b_id = None
            elif side == "a" and m.team_a_id is not None:
                if m.team_b_id is None:
                    m.team_b_id = m.team_a_id
                m.team_a_id = None
            m.updated_by = actor.pk
        await self._repo.flush()

        # 3) 새 배치 기준으로 부전승을 다시 태운다.
        for m in round1.values():
            if m.team_a_id is not None or m.team_b_id is not None:
                self._maybe_auto_resolve_round1(league, by_round_slot, total_rounds, m)

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
                # 부전승(영구 공백) 자리에는 팀을 놓을 수 없다 — 놓아 봐야 그 팀은 영원히
                # 안 붙는데 화면에는 배정된 것처럼 보인다. 부전승을 받게 하려면 같은 칸의
                # 반대쪽에 놓으면 된다.
                if editable[a.match_id].bye_side == a.side:
                    raise ValidationError("부전승 자리에는 팀을 배정할 수 없습니다.")
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

