from datetime import date

from sqlalchemy import Integer, Row, Select, and_, case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.domain.matches.models import Match, MatchParticipant, MatchResult
from app.domain.members.models import Member, ReplayAlias


class MatchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _base_query(self) -> Select[tuple[Match]]:
        return select(Match).options(
            selectinload(Match.participants),
            selectinload(Match.attachment),
            selectinload(Match.result_row),
            selectinload(Match.creator),
        )

    async def get(self, match_id: int) -> Match | None:
        stmt = self._base_query().where(Match.id == match_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_match_no(self, match_no: str) -> Match | None:
        stmt = self._base_query().where(Match.match_no == match_no)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def next_match_no_suffix(self, base: str) -> int:
        # 같은 12자리(YYMMDDHHMMSS) base를 쓰는 행 중 가장 큰 2자리 일련번호 다음 값을
        # 돌려준다 — 문자열 뒤 2자리를 정수로 잘라 비교(같은 자릿수라 문자열 정렬=숫자
        # 정렬이지만 명시적으로 캐스팅해 안전하게 최댓값을 구한다).
        suffix_expr = func.cast(func.substr(Match.match_no, len(base) + 1, 2), Integer)
        stmt = select(func.max(suffix_expr)).where(Match.match_no.like(f"{base}%"))
        max_suffix = (await self._session.execute(stmt)).scalar_one_or_none()
        return 0 if max_suffix is None else max_suffix + 1

    def add(self, match: Match) -> None:
        self._session.add(match)

    async def delete(self, match: Match) -> None:
        await self._session.delete(match)

    async def flush(self) -> None:
        await self._session.flush()

    async def refresh(self, match: Match) -> Match:
        """commit 이후 participants까지 eager load 된 상태로 다시 읽어온다.
        session.refresh(attribute_names=[...])는 참가자 목록을 새로 로드해주지 않아,
        이후 응답 직렬화 시 동기 컨텍스트에서 lazy-load 예외가 난다."""
        refreshed = await self.get(match.id)
        assert refreshed is not None
        return refreshed

    def _member_alias_join(self, participant_col):
        """참가자의 player_name을 등록된 회원으로 이어주는 조인 대상 — kind='member'인
        replay_aliases 행만 매칭되므로, 매칭되면 그 참가자는 등록된 회원이라는 뜻이고
        매칭이 안 되면(outerjoin이라 NULL) 컴퓨터/비회원/미분류라는 뜻이다. 호출부마다
        독립된 조인이 필요해(같은 경기 안 여러 참가자를 동시에 비교하는 쿼리들) 매번
        새로 aliased()한다."""
        member_alias = aliased(ReplayAlias)
        condition = and_(member_alias.raw_name == participant_col, member_alias.kind == "member")
        return member_alias, condition

    def _participant_term_exists(self, term: str):
        # 참가자(match_participants) 중 이 경기(Match.id)에 속하면서, 닉네임/배틀태그/
        # (그 회원이 등록한 모든 게임 아이디)/이 경기에서 실제로 쓴 이름 중 하나라도 이
        # 검색어를 포함하는 사람이 있는지 — EXISTS로 확인한다(메인 쿼리에 JOIN하면 LIMIT
        # 적용 전에 행이 참가자 수만큼 불어난다).
        like = f"%{term}%"
        own_alias, own_condition = self._member_alias_join(MatchParticipant.player_name)
        return exists(
            select(1)
            .select_from(MatchParticipant)
            .outerjoin(own_alias, own_condition)
            .outerjoin(Member, Member.pk == own_alias.member_pk)
            .outerjoin(ReplayAlias, ReplayAlias.member_pk == Member.pk)
            .where(
                MatchParticipant.match_id == Match.id,
                or_(
                    Member.nickname.ilike(like),
                    Member.battletag.ilike(like),
                    ReplayAlias.raw_name.ilike(like),
                    MatchParticipant.player_name.ilike(like),
                ),
            )
        )

    def _same_team_lineup_exists(self, member_pks: list[int]):
        """이 회원들이 "정확히" 같은 편이었던 경기인지 — 팀 랭킹에서 팀 하나를 눌렀을 때
        그 팀이 실제로 함께 뛴 경기만 보여주기 위한 조건이다. 단순히 "전원이 참가한 경기"로
        찾으면 서로 상대편이었던 경기까지 딸려오고, "이 인원을 포함하는 편"으로만 찾으면
        실제로는 한두 명이 더 낀(다른) 편이었던 경기까지 이 팀의 역사로 잘못 섞여 보인다
        (실제로 지적받은 문제 — 3인 팀 조회에 4인 편이었던 경기가 딸려옴). 그래서 (1) 이
        인원 전부가 같은 편이었는지 + (2) 그 편의 실제 인원수(컴퓨터/비회원 포함)가 정확히
        이 인원수와 같은지, 둘 다 확인한다.

        기준이 되는 첫 번째 회원의 참가행(anchor)을 잡고, 나머지는 그 행과 team 값이 같은
        참가행이 있는지를 각각 EXISTS로 확인한다(팀이 team1/team2 둘뿐이라 이걸로 충분하다).
        메인 쿼리에 JOIN하면 LIMIT 적용 전에 행이 참가자 수만큼 불어나므로 전부 EXISTS로 쓴다.

        각 참가행이 어느 회원인지는 player_name을 replay_aliases(kind='member')와 조인해
        구한다(더 이상 member_pk 컬럼이 없다).

        안쪽 EXISTS에서 경기를 가리킬 때 Match.id가 아니라 anchor.match_id를 쓴다 — 바깥
        테이블(matches)을 안쪽에서 참조하면 SQLAlchemy가 그 테이블을 서브쿼리의 FROM에 다시
        넣어버려(상관 서브쿼리가 아니라 카티전 곱이 된다) 조건이 사실상 항상 참이 됐다."""
        anchor = aliased(MatchParticipant)
        anchor_alias, anchor_condition = self._member_alias_join(anchor.player_name)
        conditions = [anchor.match_id == Match.id, anchor_alias.member_pk == member_pks[0]]
        for pk in member_pks[1:]:
            mate = aliased(MatchParticipant)
            mate_alias, mate_condition = self._member_alias_join(mate.player_name)
            conditions.append(
                exists(
                    select(1)
                    .select_from(mate)
                    .outerjoin(mate_alias, mate_condition)
                    .where(
                        mate.match_id == anchor.match_id,
                        mate_alias.member_pk == pk,
                        mate.team == anchor.team,
                    )
                )
            )
        side_size = aliased(MatchParticipant)
        conditions.append(
            select(func.count())
            .select_from(side_size)
            .where(side_size.match_id == anchor.match_id, side_size.team == anchor.team)
            .scalar_subquery() == len(member_pks)
        )
        anchor_stmt = select(1).select_from(anchor).outerjoin(anchor_alias, anchor_condition)
        return exists(anchor_stmt.where(and_(*conditions)))

    def _apply_list_filters(
        self,
        stmt: Select,
        *,
        date_from: date | None,
        date_to: date | None,
        match_type: str | None,
        terms: list[str],
        match_all_terms: bool,
        has_placeholder: bool = False,
        match_no: str | None = None,
        team_member_pks: list[int] | None = None,
    ) -> Select:
        """목록 조회(list_page)와 총 건수(count_page)가 공유하는 필터 조건 — 정렬/커서/limit은
        건수 집계와 무관하므로 여기 포함하지 않는다."""
        if match_no is not None:
            # 정확히 일치가 아니라 부분 일치(LIKE) — 14자리 전체를 다 기억/타이핑하지
            # 않아도 앞/뒤/중간 일부만으로 찾을 수 있게 한다(프론트는 일치한 부분을
            # 하이라이트로 보여준다).
            stmt = stmt.where(Match.match_no.like(f"%{match_no}%"))
        if match_type is not None:
            stmt = stmt.where(Match.match_type == match_type)
        if date_from is not None:
            stmt = stmt.where(Match.match_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(Match.match_date <= date_to)
        if terms:
            conditions = [self._participant_term_exists(t) for t in terms]
            stmt = stmt.where(and_(*conditions) if match_all_terms else or_(*conditions))
        if team_member_pks:
            stmt = stmt.where(self._same_team_lineup_exists(team_member_pks))
        # 관리자 "유저 매핑 관리" 화면 전용 — 컴퓨터/비회원으로 분류된 참가자가 하나라도
        # 있는 경기만 골라낸다. player_name을 replay_aliases와 조인해 kind가 컴퓨터/
        # 비회원인지로 판단한다(수기등록 슬롯도 예약 player_name 덕분에 이 조인 하나로
        # 똑같이 걸린다).
        if has_placeholder:
            stmt = stmt.where(
                exists(
                    select(1)
                    .select_from(MatchParticipant)
                    .join(ReplayAlias, ReplayAlias.raw_name == MatchParticipant.player_name)
                    .where(
                        MatchParticipant.match_id == Match.id,
                        ReplayAlias.kind.in_(("computer", "unregistered")),
                    )
                )
            )
        return stmt

    async def list_page(
        self,
        *,
        cursor: str | None,
        limit: int,
        sort: str,
        date_from: date | None,
        date_to: date | None,
        match_type: str | None,
        terms: list[str],
        match_all_terms: bool,
        has_placeholder: bool = False,
        match_no: str | None = None,
        team_member_pks: list[int] | None = None,
    ) -> tuple[list[Match], bool]:
        stmt = self._apply_list_filters(
            self._base_query(),
            date_from=date_from, date_to=date_to, match_type=match_type,
            terms=terms, match_all_terms=match_all_terms,
            has_placeholder=has_placeholder, match_no=match_no,
            team_member_pks=team_member_pks,
        )

        # match_no(YYMMDDHHMMSS+2자리)는 등록 순서(id)가 아니라 실제 경기가 열린 시각
        # 기준으로 매겨진 불변 키라 — 나중에 등록되는 리플레이가 그보다 이른 시각의
        # 경기일 수도 있어(id는 등록 순서일 뿐 실제 시각과 무관), 목록 정렬도 id 대신
        # 이 값 하나로 한다. 14자리 고정폭 숫자 문자열이라 문자열 정렬 = 시각 정렬이다.
        descending = sort != "oldest"
        if descending:
            stmt = stmt.order_by(Match.match_no.desc())
        else:
            stmt = stmt.order_by(Match.match_no.asc())

        if cursor is not None:
            if descending:
                stmt = stmt.where(Match.match_no < cursor)
            else:
                stmt = stmt.where(Match.match_no > cursor)

        # 다음 페이지가 있는지 알기 위해 하나 더 가져오고, 실제로 돌려줄 때는 잘라낸다.
        stmt = stmt.limit(limit + 1)
        rows = list((await self._session.execute(stmt)).scalars().all())
        has_more = len(rows) > limit
        return rows[:limit], has_more

    async def count_page(
        self,
        *,
        date_from: date | None,
        date_to: date | None,
        match_type: str | None,
        terms: list[str],
        match_all_terms: bool,
        has_placeholder: bool = False,
        match_no: str | None = None,
        team_member_pks: list[int] | None = None,
    ) -> int:
        """무한스크롤로 일부만 로드된 상태에서도 화면에 정확한 총 건수를 보여주기 위한
        조회 — list_page와 완전히 같은 필터 조건을 커서/정렬 없이 그대로 적용한다."""
        stmt = self._apply_list_filters(
            select(func.count(Match.id)),
            date_from=date_from, date_to=date_to, match_type=match_type,
            terms=terms, match_all_terms=match_all_terms,
            has_placeholder=has_placeholder, match_no=match_no,
            team_member_pks=team_member_pks,
        )
        return (await self._session.execute(stmt)).scalar_one()

    def _apply_common_match_filters(
        self,
        stmt: Select,
        *,
        date_from: date | None,
        date_to: date | None,
        match_type: str | None,
    ) -> Select:
        """aggregate_stats/raw_eapm_ecmd_rows가 공통으로 쓰는 기간/유형 필터."""
        if date_from is not None:
            stmt = stmt.where(Match.match_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(Match.match_date <= date_to)
        if match_type is not None:
            stmt = stmt.where(Match.match_type == match_type)
        return stmt

    async def aggregate_stats(
        self,
        *,
        member_pks: list[int],
        date_from: date | None,
        date_to: date | None,
        match_type: str | None,
    ) -> list[Row]:
        """member_pk, race 단위로 묶은 전적/평균 APM·EAPM·커맨드수 원본 집계 행. 종족별로 나눠서
        받아오고, "전체" 기준이 필요한 쪽(overall)은 호출부에서 이 행들을 합산해서 만든다.
        member_pk는 컬럼이 아니라 player_name → replay_aliases(kind='member') 조인으로 구한다."""

        def _avg_pair(col):
            total = func.sum(case((col.is_not(None), col), else_=0))
            count = func.sum(case((col.is_not(None), 1), else_=0))
            return total, count

        apm_sum, apm_cnt = _avg_pair(MatchParticipant.apm)
        eapm_sum, eapm_cnt = _avg_pair(MatchParticipant.eapm)
        cmd_sum, cmd_cnt = _avg_pair(MatchParticipant.cmd_count)
        ecmd_sum, _ecmd_cnt = _avg_pair(MatchParticipant.effective_cmd_count)
        # 유효커맨드는 총합이 아니라 "분당" 값으로 보여줘야 경기 길이가 제각각이어도 공정하게
        # 비교된다 — effective_cmd_count가 있는 행(=리플레이로 등록된 경기, 항상 duration_seconds도
        # 같이 채워져 있다)의 경기 시간만 더해서, 서비스 레이어에서 ecmd_sum / (이 합/60)으로 계산한다.
        ecmd_duration_sum = func.sum(
            case(
                (MatchParticipant.effective_cmd_count.is_not(None), MatchResult.duration_seconds),
                else_=0,
            )
        )

        member_alias, member_condition = self._member_alias_join(MatchParticipant.player_name)
        stmt = (
            select(
                member_alias.member_pk,
                MatchParticipant.race,
                func.count().label("plays"),
                func.sum(case((MatchResult.result == "draw", 1), else_=0)).label("draws"),
                func.sum(case((MatchResult.result == MatchParticipant.team, 1), else_=0)).label("wins"),
                apm_sum.label("apm_sum"),
                apm_cnt.label("apm_cnt"),
                eapm_sum.label("eapm_sum"),
                eapm_cnt.label("eapm_cnt"),
                cmd_sum.label("cmd_sum"),
                cmd_cnt.label("cmd_cnt"),
                ecmd_sum.label("ecmd_sum"),
                ecmd_duration_sum.label("ecmd_duration_sum"),
            )
            .select_from(MatchParticipant)
            .join(Match, Match.id == MatchParticipant.match_id)
            .join(MatchResult, MatchResult.match_id == Match.id)
            .join(member_alias, member_condition)
            .where(
                member_alias.member_pk.in_(member_pks),
                MatchResult.result != "not_held",
            )
            .group_by(member_alias.member_pk, MatchParticipant.race)
        )
        stmt = self._apply_common_match_filters(
            stmt, date_from=date_from, date_to=date_to, match_type=match_type,
        )

        return list((await self._session.execute(stmt)).all())

    async def raw_eapm_ecmd_rows(
        self,
        *,
        member_pks: list[int],
        date_from: date | None,
        date_to: date | None,
        match_type: str | None,
    ) -> list[Row]:
        """member_pk/race 단위로 미리 합산하지 않은 경기별 원본 유효APM·유효커맨드값.
        aggregate_stats는 SQL에서 이미 합계/개수로 뭉쳐서 내려주기 때문에, 평균을 내기
        전에 회원 한 명 안에서 유독 튀는(편차가 심한) 경기 하나만 골라 빼는 계산(서비스
        레이어의 _trimmed_avg_eapm/_trimmed_avg_ecmd)에는 쓸 수 없어 원본 단위로 따로 받는다."""
        member_alias, member_condition = self._member_alias_join(MatchParticipant.player_name)
        stmt = (
            select(
                member_alias.member_pk,
                MatchParticipant.race,
                MatchParticipant.eapm,
                MatchParticipant.effective_cmd_count,
                MatchResult.duration_seconds,
            )
            .select_from(MatchParticipant)
            .join(Match, Match.id == MatchParticipant.match_id)
            .join(MatchResult, MatchResult.match_id == Match.id)
            .join(member_alias, member_condition)
            .where(
                member_alias.member_pk.in_(member_pks),
                MatchResult.result != "not_held",
            )
        )
        stmt = self._apply_common_match_filters(
            stmt, date_from=date_from, date_to=date_to, match_type=match_type,
        )

        return list((await self._session.execute(stmt)).all())

    async def head_to_head_rows(
        self,
        *,
        member_pks: list[int],
        date_from: date | None,
        date_to: date | None,
        match_type: str | None,
        race: str | None,
    ) -> list[Row]:
        """(회원, 그 회원이 상대편으로 만난 회원) 쌍마다의 전적 — 순위 동률을 승자승(맞대결)과
        공통상대 성적으로 가르는 데 쓴다. 상대(opponent)는 member_pks로 좁히지 않는다 —
        "공통으로 붙어본 상대"에는 지금 동률인 두 사람 말고도 클럽의 아무나 들어올 수 있어서다.
        팀전이면 상대팀 전원 각각과 한 번씩 붙은 것으로 센다(1:1이면 자연히 한 명).
        race 필터는 "그 경기에서 본인이 고른 종족" 기준 — 개인 전적 집계(aggregate_stats)와 같다."""
        opponent = aliased(MatchParticipant)
        self_alias, self_condition = self._member_alias_join(MatchParticipant.player_name)
        opponent_alias, opponent_condition = self._member_alias_join(opponent.player_name)
        stmt = (
            select(
                self_alias.member_pk,
                opponent_alias.member_pk.label("opponent_pk"),
                func.count().label("plays"),
                func.sum(case((MatchResult.result == "draw", 1), else_=0)).label("draws"),
                func.sum(case((MatchResult.result == MatchParticipant.team, 1), else_=0)).label("wins"),
            )
            .select_from(MatchParticipant)
            .join(Match, Match.id == MatchParticipant.match_id)
            .join(MatchResult, MatchResult.match_id == Match.id)
            .join(self_alias, self_condition)
            .join(
                opponent,
                and_(
                    opponent.match_id == MatchParticipant.match_id,
                    opponent.team != MatchParticipant.team,
                ),
            )
            .join(opponent_alias, opponent_condition)
            .where(
                self_alias.member_pk.in_(member_pks),
                MatchResult.result != "not_held",
            )
            .group_by(self_alias.member_pk, opponent_alias.member_pk)
        )
        if race is not None and race != "all":
            stmt = stmt.where(MatchParticipant.race == race)
        stmt = self._apply_common_match_filters(
            stmt, date_from=date_from, date_to=date_to, match_type=match_type,
        )
        return list((await self._session.execute(stmt)).all())

    async def team_participant_rows(self) -> list[Row]:
        """팀랭킹 집계용 원본 — 전체 기간의 모든 경기 참가행(컴퓨터/비회원 포함)과 그 경기
        결과. "어떤 회원들이 한 팀이었나"는 (match_id, team)으로 묶어봐야 알 수 있어서 SQL에서
        미리 뭉치지 않고 행 단위로 그대로 넘긴다. 회원이 아닌 행(컴퓨터/비회원, member_pk가
        NULL)도 일부러 포함한다 — 서비스 쪽에서 "이 편에 컴퓨터/비회원이 한 명이라도 섞여
        있는지"를 판단해야 하기 때문이다(섞여 있으면 남은 실제 회원끼리를 별개의 팀으로
        잘못 집계하게 된다 — 예: 3:3에 컴퓨터 1명이 끼면 실제 회원 2명만 남아 2인 팀처럼
        보이는데, 실제로는 그 둘이 2:2를 뛴 적이 없다)."""
        member_alias, member_condition = self._member_alias_join(MatchParticipant.player_name)
        stmt = (
            select(
                MatchParticipant.match_id,
                MatchParticipant.team,
                member_alias.member_pk,
                MatchResult.result,
            )
            .select_from(MatchParticipant)
            .join(Match, Match.id == MatchParticipant.match_id)
            .join(MatchResult, MatchResult.match_id == Match.id)
            .outerjoin(member_alias, member_condition)
            .where(MatchResult.result != "not_held")
        )
        return list((await self._session.execute(stmt)).all())

    async def earliest_match_date(self) -> date | None:
        # 랭킹 화면의 "이전" 버튼 비활성화 판단용 — 실제 결과가 있는 가장 이른 날짜.
        stmt = select(func.min(Match.match_date)).where(
            MatchResult.result != "not_held"
        ).select_from(Match).join(MatchResult, MatchResult.match_id == Match.id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_game_started_ats(self):
        # 문자열/타임존 표현이 서로 달라도(입력 "Z" vs 저장 "+00:00", SQLite의 tz 소실 등)
        # 정확히 매칭하려면 SQL WHERE IN 비교 대신 값을 전부 가져와 파이썬에서(서비스 계층에서)
        # UTC로 정규화해 비교하는 편이 드라이버/방언에 안전하다. 리플레이 중복확인은 admin이
        # 배치 업로드할 때만 호출되는 저빈도 동작이라 이 정도 전체 조회는 무겁지 않다.
        stmt = select(MatchResult.game_started_at).where(MatchResult.game_started_at.is_not(None))
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_replay_name_classifications(self, raw_names: list[str]) -> list[ReplayAlias]:
        if not raw_names:
            return []
        stmt = select(ReplayAlias).where(ReplayAlias.raw_name.in_(raw_names), ReplayAlias.kind != "member")
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_replay_name_classification(self, raw_name: str) -> ReplayAlias | None:
        stmt = select(ReplayAlias).where(ReplayAlias.raw_name == raw_name, ReplayAlias.kind != "member")
        return (await self._session.execute(stmt)).scalar_one_or_none()

    def add_replay_name_classification(self, entry: ReplayAlias) -> None:
        self._session.add(entry)

    async def replay_alias_exists(self, raw_name: str) -> bool:
        """kind와 무관하게 이 이름의 매핑 행이 이미 있는지 — raw_name은 테이블 전체에서
        유일하므로(uq_replay_aliases_raw_name), 새로 넣기 전에 이걸로 확인해야 한다.
        get_replay_name_classification은 kind='member'인 행을 일부러 빼고 보므로, 그걸로
        판단하면 이미 회원에게 등록된 이름을 또 넣으려다 유니크 제약에 걸린다."""
        stmt = select(exists().where(ReplayAlias.raw_name == raw_name))
        return bool((await self._session.execute(stmt)).scalar())

    async def get_alias_by_raw_name(self, raw_name: str) -> ReplayAlias | None:
        """kind와 무관하게 이 이름의 매핑 행 자체를 돌려준다 — 수기입력에서 회원 슬롯에
        새 player_name을 쓸 때, 그 이름이 이미 다른 대상으로 등록돼 있는지 확인하는 데 쓴다."""
        stmt = select(ReplayAlias).where(ReplayAlias.raw_name == raw_name)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_all_replay_aliases(self) -> list[ReplayAlias]:
        stmt = select(ReplayAlias).options(selectinload(ReplayAlias.member))
        return list((await self._session.execute(stmt)).scalars().all())

    async def delete_replay_alias(self, raw_name: str) -> None:
        # raw_name은 kind와 무관하게 replay_aliases 테이블 전체에서 유일하므로, 이 한 번의
        # 삭제로 예전에 회원 별칭으로 등록돼 있었든 컴퓨터/비회원으로 분류돼 있었든 깨끗이
        # 지워진다 — 새 대상으로 다시 매핑하기 전에 항상 먼저 호출한다.
        stmt = select(ReplayAlias).where(ReplayAlias.raw_name == raw_name)
        existing = (await self._session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            await self._session.delete(existing)

    async def raw_name_has_any_participants(self, raw_name: str) -> bool:
        # 유저 매핑 관리 화면의 "삭제"(매핑 데이터 자체를 지우기)를 허용해도 되는지
        # 판단하는 기준 — 회원 연결 여부와 무관하게(이미 회원으로 소급 연결된 경기까지
        # 포함) 이 player_name이 걸린 경기 참가 기록이 하나라도 있으면, 그 별칭 행만 지워도
        # 실제 경기 기록은 그대로 남아 있어(player_name은 영구 보존) list_placeholder_
        # raw_names_with_last_seen을 통해 곧바로 "미지정"으로 되돌아와 버린다 — 삭제한
        # 게 아니라 그냥 되돌리기와 같아져 사용자 의도(목록에서 완전히 사라짐)와 어긋난다.
        stmt = select(exists().where(MatchParticipant.player_name == raw_name))
        return bool((await self._session.execute(stmt)).scalar())

    async def list_placeholder_raw_names_with_last_seen(self) -> list[tuple[str, date]]:
        # "회원으로 연결되지 않았다"인지는 이 player_name으로 매칭되는 kind='member'
        # replay_aliases 행이 있는지로 판단한다(더 이상 member_pk 컬럼이 없다) — 컴퓨터/
        # 비회원으로 이미 분류된 이름과, 아직 아무 분류도 없는 이름을 모두 포함한다(서비스
        # 레이어의 list_replay_name_mappings가 이 중 "이미 분류된" 것들은 last_seen만
        # 가져다 쓰고, "아직 미분류"인 것만 새 entry로 만든다). 마지막으로 나온 경기
        # 날짜가 필요해 matches와 조인해 그룹별 최댓값을 구한다.
        stmt = (
            select(MatchParticipant.player_name, func.max(Match.match_date))
            .join(Match, Match.id == MatchParticipant.match_id)
            .where(
                ~exists(
                    select(1).where(
                        ReplayAlias.raw_name == MatchParticipant.player_name,
                        ReplayAlias.kind == "member",
                    )
                )
            )
            .group_by(MatchParticipant.player_name)
        )
        rows = (await self._session.execute(stmt)).all()
        return [(raw_name, last_seen) for raw_name, last_seen in rows]

    async def resolve_placeholder_raw_name_to_member(self, raw_name: str, member_pk: int) -> None:
        # 회원 매칭은 이제 match_participants가 아니라 replay_aliases 행 하나(kind='member')로
        # 전부 표현된다 — 그 alias 행이 호출부(set_replay_name_mapping)에서 이미 만들어지므로
        # 여기서는 더 할 일이 없다. 과거에는 이 player_name으로 남아있던 기존 경기 참가
        # 기록(member_pk NULL)을 전부 이 회원으로 소급 연결하는 UPDATE가 필요했지만,
        # 지금은 player_name → replay_aliases 조회가 매번 그 자리에서 이뤄지므로 alias 행
        #하나만 있으면 과거 경기까지 자동으로 전부 이 회원으로 연결된다.
        pass

    async def revert_raw_name_to_unresolved(self, raw_name: str) -> None:
        # 유저 매핑 관리 화면에서 이미 회원으로 연결된 매핑을 다시 "미지정"으로 되돌릴 때
        # 쓴다 — delete_replay_alias가 replay_aliases 행을 지우면 그 즉시 player_name →
        # replay_aliases 조회가 끊겨 자동으로 미지정 취급된다(더 이상 match_participants에
        # 따로 되돌릴 컬럼이 없다).
        pass

