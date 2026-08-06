from datetime import date

from sqlalchemy import Integer, Row, Select, and_, case, delete, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.domain.game_results.models import (
    GameResult,
    GameResultParticipant,
    GameOutcome,
    MinimapImage,
    Replay,
    ReplayMap,
)
from app.domain.members.models import Member, ReplayAlias


class GameResultRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _base_query(self) -> Select[tuple[GameResult]]:
        return select(GameResult).options(
            selectinload(GameResult.participants),
            selectinload(GameResult.result_row).selectinload(GameOutcome.replay),
            selectinload(GameResult.creator),
            # 댓글(메모)과 그 안의 언급/작성자 — 목록/상세 응답에 함께 실어야 하므로 eager
            # 로드한다(mentions/creator는 관계 자체가 lazy="selectin"이라 자동으로 딸려온다).
        )

    async def get(self, match_id: int) -> GameResult | None:
        stmt = self._base_query().where(GameResult.id == match_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    # IN 목록을 이만큼씩 끊어 묻는다. asyncpg는 바인드 파라미터가 32767개를 넘으면 질의를
    # 준비(prepare)하는 단계에서 통째로 실패한다(실측: 32767 통과, 33000 InterfaceError).
    # 한 줄에 담기는 경기가 그만큼 많을 일은 없지만, 목록이 커질수록 이 목록도 같이
    # 길어지는 구조라 상한을 코드가 아니라 데이터가 정하게 두면 안 된다.
    _IN_CHUNK = 5000

    async def get_many(self, match_ids: list[int]) -> list[GameResult]:
        """여러 경기를 한 번에 — 활동 목록이 한 줄(한 자리에서 이어 친 묶음)을 채울 때 쓴다.
        하나씩 부르면 줄에 담긴 경기 수만큼 질의가 나간다."""
        if not match_ids:
            return []
        out: list[GameResult] = []
        for i in range(0, len(match_ids), self._IN_CHUNK):
            stmt = self._base_query().where(GameResult.id.in_(match_ids[i:i + self._IN_CHUNK]))
            out.extend((await self._session.execute(stmt)).scalars().unique().all())
        return out

    async def get_by_match_no(self, match_no: str) -> GameResult | None:
        stmt = self._base_query().where(GameResult.match_no == match_no)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def next_match_no_suffix(self, base: str) -> int:
        # 같은 12자리(YYMMDDHHMMSS) base를 쓰는 행 중 가장 큰 2자리 일련번호 다음 값을
        # 돌려준다 — 문자열 뒤 2자리를 정수로 잘라 비교(같은 자릿수라 문자열 정렬=숫자
        # 정렬이지만 명시적으로 캐스팅해 안전하게 최댓값을 구한다).
        suffix_expr = func.cast(func.substr(GameResult.match_no, len(base) + 1, 2), Integer)
        stmt = select(func.max(suffix_expr)).where(GameResult.match_no.like(f"{base}%"))
        max_suffix = (await self._session.execute(stmt)).scalar_one_or_none()
        return 0 if max_suffix is None else max_suffix + 1

    def add(self, match: GameResult) -> None:
        self._session.add(match)

    async def delete(self, match: GameResult) -> None:
        await self._session.delete(match)

    async def delete_all_matches(self) -> int:
        # 참가자/첨부/결과는 FK ondelete=CASCADE라 matches 한 방 삭제로 함께 지워진다.
        result = await self._session.execute(delete(GameResult))
        return int(result.rowcount or 0)

    async def flush(self) -> None:
        await self._session.flush()

    async def refresh(self, match: GameResult) -> GameResult:
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
        # 참가자(match_participants) 중 이 경기(GameResult.id)에 속하면서, 닉네임/배틀태그/
        # (그 회원이 등록한 모든 게임 아이디)/이 경기에서 실제로 쓴 이름 중 하나라도 이
        # 검색어를 포함하는 사람이 있는지 — EXISTS로 확인한다(메인 쿼리에 JOIN하면 LIMIT
        # 적용 전에 행이 참가자 수만큼 불어난다).
        like = f"%{term}%"
        own_alias, own_condition = self._member_alias_join(GameResultParticipant.player_name)
        return exists(
            select(1)
            .select_from(GameResultParticipant)
            .outerjoin(own_alias, own_condition)
            .outerjoin(Member, Member.pk == own_alias.member_pk)
            .outerjoin(ReplayAlias, ReplayAlias.member_pk == Member.pk)
            .where(
                GameResultParticipant.match_id == GameResult.id,
                or_(
                    Member.nickname.ilike(like),
                    Member.battletag.ilike(like),
                    ReplayAlias.raw_name.ilike(like),
                    GameResultParticipant.player_name.ilike(like),
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

        안쪽 EXISTS에서 경기를 가리킬 때 GameResult.id가 아니라 anchor.match_id를 쓴다 — 바깥
        테이블(matches)을 안쪽에서 참조하면 SQLAlchemy가 그 테이블을 서브쿼리의 FROM에 다시
        넣어버려(상관 서브쿼리가 아니라 카티전 곱이 된다) 조건이 사실상 항상 참이 됐다."""
        anchor = aliased(GameResultParticipant)
        anchor_alias, anchor_condition = self._member_alias_join(anchor.player_name)
        conditions = [anchor.match_id == GameResult.id, anchor_alias.member_pk == member_pks[0]]
        for pk in member_pks[1:]:
            mate = aliased(GameResultParticipant)
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
        # 편 인원수 정확히 일치 조건은 여러 명(특정 라인업)을 조회할 때만 필요하다 — 3인
        # 라인업 조회에 그 셋을 포함한 4인 편이 딸려오지 않게 막는 용도다. 한 명만 넘길
        # 때는(개인 랭킹 상세가 그 회원의 팀경기 이력 전체를 부를 때) 편 인원수를 따지면
        # 안 된다 — "1인 편"만 남아 2:2·3:3 팀경기가 통째로 빠져 이력이 안 나왔다(실제로
        # 지적받은 버그). 이 경우 조건은 "그 회원이 이 경기에 참가했는가"뿐이고, 개인전/팀전
        # 구분은 호출부의 match_type 필터가 맡는다.
        if len(member_pks) > 1:
            side_size = aliased(GameResultParticipant)
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
        team_member_pks: list[int] | None = None,
    ) -> Select:
        """목록 조회(list_page)와 총 건수(count_page)가 공유하는 필터 조건 — 정렬/커서/limit은
        건수 집계와 무관하므로 여기 포함하지 않는다."""
        if match_type is not None:
            stmt = stmt.where(GameResult.match_type == match_type)
        if date_from is not None:
            stmt = stmt.where(GameResult.match_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(GameResult.match_date <= date_to)
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
                    .select_from(GameResultParticipant)
                    .join(ReplayAlias, ReplayAlias.raw_name == GameResultParticipant.player_name)
                    .where(
                        GameResultParticipant.match_id == GameResult.id,
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
        team_member_pks: list[int] | None = None,
    ) -> tuple[list[GameResult], bool]:
        stmt = self._apply_list_filters(
            self._base_query(),
            date_from=date_from, date_to=date_to, match_type=match_type,
            terms=terms, match_all_terms=match_all_terms,
            has_placeholder=has_placeholder,
            team_member_pks=team_member_pks,
        )

        # match_no(YYMMDDHHMMSS+2자리)는 등록 순서(id)가 아니라 실제 경기가 열린 시각
        # 기준으로 매겨진 불변 키라 — 나중에 등록되는 리플레이가 그보다 이른 시각의
        # 경기일 수도 있어(id는 등록 순서일 뿐 실제 시각과 무관), 목록 정렬도 id 대신
        # 이 값 하나로 한다. 14자리 고정폭 숫자 문자열이라 문자열 정렬 = 시각 정렬이다.
        descending = sort != "oldest"
        if descending:
            stmt = stmt.order_by(GameResult.match_no.desc())
        else:
            stmt = stmt.order_by(GameResult.match_no.asc())

        if cursor is not None:
            if descending:
                stmt = stmt.where(GameResult.match_no < cursor)
            else:
                stmt = stmt.where(GameResult.match_no > cursor)

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
        team_member_pks: list[int] | None = None,
    ) -> int:
        """무한스크롤로 일부만 로드된 상태에서도 화면에 정확한 총 건수를 보여주기 위한
        조회 — list_page와 완전히 같은 필터 조건을 커서/정렬 없이 그대로 적용한다."""
        stmt = self._apply_list_filters(
            select(func.count(GameResult.id)),
            date_from=date_from, date_to=date_to, match_type=match_type,
            terms=terms, match_all_terms=match_all_terms,
            has_placeholder=has_placeholder,
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
        """aggregate_stats/raw_metric_rows가 공통으로 쓰는 기간/유형 필터."""
        if date_from is not None:
            stmt = stmt.where(GameResult.match_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(GameResult.match_date <= date_to)
        if match_type is not None:
            stmt = stmt.where(GameResult.match_type == match_type)
        return stmt

    async def aggregate_stats(
        self,
        *,
        member_pks: list[int],
        date_from: date | None,
        date_to: date | None,
        match_type: str | None,
    ) -> list[Row]:
        """member_pk, race 단위로 묶은 전적(판수/승/무) 집계 행. 종족별로 나눠서 받아오고,
        "전체" 기준이 필요한 쪽(overall)은 호출부에서 이 행들을 합산해서 만든다.
        member_pk는 컬럼이 아니라 player_name → replay_aliases(kind='member') 조인으로 구한다.

        지표 평균(APM·유효APM·커맨드·유효커맨드·생산)은 여기서 내지 않는다 — 이상치를 뺀
        평균이라 경기 단위 원본이 있어야 해서 raw_metric_rows가 따로 담당한다. 예전엔 여기서도
        합계/개수 쌍을 같이 내려줬지만 서비스가 전부 덮어써서 쓰이지 않았다."""
        member_alias, member_condition = self._member_alias_join(GameResultParticipant.player_name)
        stmt = (
            select(
                member_alias.member_pk,
                GameResultParticipant.race,
                func.count().label("plays"),
                func.sum(case((GameOutcome.result == "draw", 1), else_=0)).label("draws"),
                func.sum(case((GameOutcome.result == GameResultParticipant.team, 1), else_=0)).label("wins"),
            )
            .select_from(GameResultParticipant)
            .join(GameResult, GameResult.id == GameResultParticipant.match_id)
            .join(GameOutcome, GameOutcome.match_id == GameResult.id)
            .join(member_alias, member_condition)
            .where(
                member_alias.member_pk.in_(member_pks),
                GameOutcome.result != "not_held",
            )
            .group_by(member_alias.member_pk, GameResultParticipant.race)
        )
        stmt = self._apply_common_match_filters(
            stmt, date_from=date_from, date_to=date_to, match_type=match_type,
        )

        return list((await self._session.execute(stmt)).all())

    async def rivalry_rows(
        self,
        *,
        date_from: date | None,
        date_to: date | None,
        team: bool = False,
    ) -> list[Row]:
        """상성(상대전적) 집계용 원본 행 — (match_id, 결과, 팀, member_pk). 기본은 1:1
        경기(match_type='0101')만, team=True면 반대로 팀전(0101이 아닌 경기)만 가져와
        서비스 레이어에서 개인 단위 쌍으로 환산한다(요청: 상성맵 팀전 탭). 회원 매칭은
        aggregate_stats와 같은 replay_aliases(kind='member') 조인이라, 비회원/컴퓨터가
        낀 경기는 그 참가자 행이 아예 안 나와 서비스 레이어 검증에서 자연히 걸러진다."""
        member_alias, member_condition = self._member_alias_join(GameResultParticipant.player_name)
        stmt = (
            select(
                GameResultParticipant.match_id,
                GameOutcome.result,
                GameResultParticipant.team,
                member_alias.member_pk,
            )
            .select_from(GameResultParticipant)
            .join(GameResult, GameResult.id == GameResultParticipant.match_id)
            .join(GameOutcome, GameOutcome.match_id == GameResult.id)
            .join(member_alias, member_condition)
            .where(GameOutcome.result != "not_held")
        )
        if team:
            stmt = stmt.where(GameResult.match_type != "0101")
        stmt = self._apply_common_match_filters(
            stmt, date_from=date_from, date_to=date_to,
            match_type=None if team else "0101",
        )
        return list((await self._session.execute(stmt)).all())

    async def raw_metric_rows(
        self,
        *,
        member_pks: list[int],
        date_from: date | None,
        date_to: date | None,
        match_type: str | None,
    ) -> list[Row]:
        """member_pk/race 단위로 미리 합산하지 않은 경기별 원본 지표값 — APM·유효APM·커맨드·
        유효커맨드·생산 다섯 가지 전부. aggregate_stats는 SQL에서 이미 합계/개수로 뭉쳐서
        내려주기 때문에, 평균을 내기 전에 회원 한 명 안에서 유독 튀는(편차가 심한) 경기
        하나만 골라 빼는 계산(서비스 레이어의 _trimmed_avg)에는 쓸 수 없어 원본 단위로
        따로 받는다."""
        member_alias, member_condition = self._member_alias_join(GameResultParticipant.player_name)
        stmt = (
            select(
                member_alias.member_pk,
                GameResultParticipant.race,
                GameResultParticipant.apm,
                GameResultParticipant.eapm,
                GameResultParticipant.cmd_count,
                GameResultParticipant.effective_cmd_count,
                GameResultParticipant.build_count,
                GameResultParticipant.build_mix,
                GameOutcome.duration_seconds,
            )
            .select_from(GameResultParticipant)
            .join(GameResult, GameResult.id == GameResultParticipant.match_id)
            .join(GameOutcome, GameOutcome.match_id == GameResult.id)
            .join(member_alias, member_condition)
            .where(
                member_alias.member_pk.in_(member_pks),
                GameOutcome.result != "not_held",
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
        opponent = aliased(GameResultParticipant)
        self_alias, self_condition = self._member_alias_join(GameResultParticipant.player_name)
        opponent_alias, opponent_condition = self._member_alias_join(opponent.player_name)
        stmt = (
            select(
                self_alias.member_pk,
                opponent_alias.member_pk.label("opponent_pk"),
                func.count().label("plays"),
                func.sum(case((GameOutcome.result == "draw", 1), else_=0)).label("draws"),
                func.sum(case((GameOutcome.result == GameResultParticipant.team, 1), else_=0)).label("wins"),
            )
            .select_from(GameResultParticipant)
            .join(GameResult, GameResult.id == GameResultParticipant.match_id)
            .join(GameOutcome, GameOutcome.match_id == GameResult.id)
            .join(self_alias, self_condition)
            .join(
                opponent,
                and_(
                    opponent.match_id == GameResultParticipant.match_id,
                    opponent.team != GameResultParticipant.team,
                ),
            )
            .join(opponent_alias, opponent_condition)
            .where(
                self_alias.member_pk.in_(member_pks),
                GameOutcome.result != "not_held",
            )
            .group_by(self_alias.member_pk, opponent_alias.member_pk)
        )
        if race is not None and race != "all":
            stmt = stmt.where(GameResultParticipant.race == race)
        stmt = self._apply_common_match_filters(
            stmt, date_from=date_from, date_to=date_to, match_type=match_type,
        )
        return list((await self._session.execute(stmt)).all())

    async def rank_replay_rows(
        self,
        *,
        match_type: str | None,
        date_from: date | None = None,
        date_to: date | None,
    ) -> list[Row]:
        """레이팅(TrueSkill) 누적 계산용 — 이 경기유형의 경기를 '시간순으로 재생'하기 위한
        원본. 각 경기의 (match_id, team, member_pk, result)에 정렬 키(game_started_at·match_date·
        match_no)를 함께 준다. 컴퓨터/비회원(member_pk=NULL)도 포함해야 팀 구성(인원수)이 맞다.

        랭킹은 이제 date_from을 여기 걸지 않는다(요청: 월별 제로베이스 대신 지난달까지의
        누적치로 상대강도를 계산해서 시작) — 그 잘라내기가 곧 '매달 전원 μ0에서 다시 시작'
        이었기 때문이다. 대신 서비스가 _replay_ratings(count_from=...)로 '점수를 세기 시작하는
        날'만 밀어, 그 앞의 경기는 상대강도를 만드는 데만 쓰이게 한다. date_to는 그대로 건다:
        지난달을 보는데 그 뒤에 친 경기가 섞이면 안 된다. 인자는 남겨 둔다 — 백테스트처럼
        정말로 특정 구간만 재생하고 싶은 호출이 있다."""
        member_alias, member_condition = self._member_alias_join(GameResultParticipant.player_name)
        stmt = (
            select(
                GameResultParticipant.match_id,
                GameResultParticipant.team,
                member_alias.member_pk,
                GameResultParticipant.race,
                GameOutcome.result,
                GameOutcome.game_started_at,
                GameResult.match_date,
                GameResult.match_no,
            )
            .select_from(GameResultParticipant)
            .join(GameResult, GameResult.id == GameResultParticipant.match_id)
            .join(GameOutcome, GameOutcome.match_id == GameResult.id)
            .outerjoin(member_alias, member_condition)
            .where(GameOutcome.result != "not_held")
        )
        stmt = self._apply_common_match_filters(
            stmt, date_from=date_from, date_to=date_to, match_type=match_type,
        )
        return list((await self._session.execute(stmt)).all())

    async def earliest_match_date(self) -> date | None:
        # 랭킹 화면의 "이전" 버튼 비활성화 판단용 — 실제 결과가 있는 가장 이른 날짜.
        stmt = select(func.min(GameResult.match_date)).where(
            GameOutcome.result != "not_held"
        ).select_from(GameResult).join(GameOutcome, GameOutcome.match_id == GameResult.id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_game_started_ats(self):
        # 문자열/타임존 표현이 서로 달라도(입력 "Z" vs 저장 "+00:00", SQLite의 tz 소실 등)
        # 정확히 매칭하려면 SQL WHERE IN 비교 대신 값을 전부 가져와 파이썬에서(서비스 계층에서)
        # UTC로 정규화해 비교하는 편이 드라이버/방언에 안전하다. 리플레이 중복확인은 admin이
        # 배치 업로드할 때만 호출되는 저빈도 동작이라 이 정도 전체 조회는 무겁지 않다.
        stmt = select(GameOutcome.game_started_at).where(GameOutcome.game_started_at.is_not(None))
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_match_id_game_started_ats(self):
        # (match_id, game_started_at) 쌍 — list_game_started_ats와 같은 이유로(방언별 tz
        # 표현 차이) SQL에서 바로 매칭하지 않고 서비스에서 UTC 정규화해 찾는다. 머지 대상
        # 경기 한 건을 game_started_at으로 지목하는 데 쓴다.
        stmt = (
            select(GameResult.id, GameOutcome.game_started_at)
            .join(GameOutcome, GameOutcome.match_id == GameResult.id)
            .where(GameOutcome.game_started_at.is_not(None))
        )
        return list((await self._session.execute(stmt)).all())

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

    async def list_replay_maps(self, hashes: list[str]) -> list[ReplayMap]:
        """미니맵 격자를 해시로 한꺼번에 가져온다 — 활동 한 화면에 여러 경기가 있고 그중
        상당수가 같은 맵이라, 경기마다 한 번씩 묻는 대신 없는 것만 모아 한 번에 받는다."""
        if not hashes:
            return []
        stmt = select(ReplayMap).where(ReplayMap.map_hash.in_(hashes))
        return list((await self._session.execute(stmt)).scalars().all())

    async def replay_map_exists(self, map_hash: str) -> bool:
        stmt = select(ReplayMap.id).where(ReplayMap.map_hash == map_hash)
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    def add_replay_map(self, row: ReplayMap) -> None:
        self._session.add(row)

    async def list_map_catalog(self) -> list[Row]:
        """제어판용 맵 목록 — 격자(22KB)는 빼고 어떤 맵이 있고 몇 경기를 치렀는지만."""
        used = (
            select(GameOutcome.map_hash, func.count().label("n"))
            .where(GameOutcome.map_hash.is_not(None))
            .group_by(GameOutcome.map_hash)
            .subquery()
        )
        stmt = (
            select(
                ReplayMap.map_hash, ReplayMap.name, ReplayMap.width, ReplayMap.height,
                ReplayMap.image_id, func.coalesce(used.c.n, 0).label("matches"),
            )
            .outerjoin(used, used.c.map_hash == ReplayMap.map_hash)
            .order_by(func.coalesce(used.c.n, 0).desc(), ReplayMap.id)
        )
        return list((await self._session.execute(stmt)).all())

    async def list_minimap_images(self) -> list[MinimapImage]:
        stmt = select(MinimapImage).order_by(MinimapImage.id)
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_minimap_image(self, image_id: int) -> MinimapImage | None:
        stmt = select(MinimapImage).where(MinimapImage.id == image_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    def add_minimap_image(self, row: MinimapImage) -> None:
        self._session.add(row)

    async def delete_minimap_image(self, image_id: int) -> None:
        # 가리키던 맵들은 image_id가 NULL이 되어(ondelete=SET NULL) 다시 격자로 그려진다.
        await self._session.execute(delete(MinimapImage).where(MinimapImage.id == image_id))

    async def assign_minimap_image(self, hashes: list[str], image_id: int | None) -> int:
        """맵 여러 개가 한 그림을 가리키게 한다(요청: 이름·판본만 다른 맵 묶기). None이면 떼어 낸다."""
        if not hashes:
            return 0
        res = await self._session.execute(
            update(ReplayMap).where(ReplayMap.map_hash.in_(hashes)).values(image_id=image_id)
        )
        return res.rowcount or 0

    async def list_all_replays(self) -> list[Row]:
        # 리플레이 전체 다운로드(운영자) + 전체 삭제 시 파일 정리용 — 저장 파일명(display_name)과
        # 저장 경로를 등록 순으로.
        stmt = select(Replay.display_name, Replay.file_path).order_by(Replay.created_at)
        return list((await self._session.execute(stmt)).all())

    async def delete_all_replays(self) -> int:
        result = await self._session.execute(delete(Replay))
        return int(result.rowcount or 0)

    async def delete_replay_alias(self, raw_name: str) -> None:
        # raw_name은 kind와 무관하게 replay_aliases 테이블 전체에서 유일하므로, 이 한 번의
        # 삭제로 예전에 회원 별칭으로 등록돼 있었든 컴퓨터/비회원으로 분류돼 있었든 깨끗이
        # 지워진다 — 새 대상으로 다시 매핑하기 전에 항상 먼저 호출한다.
        stmt = select(ReplayAlias).where(ReplayAlias.raw_name == raw_name)
        existing = (await self._session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            await self._session.delete(existing)
            # 같은 트랜잭션에서 곧바로 같은 raw_name으로 새 별칭을 INSERT하는 경우
            # (set_replay_name_mapping: 지우고 다른 대상으로 재매핑)를 위해 DELETE를 먼저
            # 내보낸다 — flush가 없으면 SQLAlchemy 유닛오브워크가 INSERT를 DELETE보다 먼저
            # 실행해 raw_name UNIQUE 제약을 위반할 수 있다(비회원도 자동 별칭 등록되면서
            # 실제로 드러난 문제).
            await self._session.flush()

    async def all_participant_player_names(self) -> set[str]:
        # 유저 매핑 목록에 "이 이름으로 등록된 경기가 있는지"(has_matches)를 한 번에 채우기
        # 위한 조회 — 회원 연결 여부와 무관하게 경기 참가 기록에 실제로 등장한 모든
        # player_name을 모은다(회원으로 소급 연결된 이름은 placeholder 조회에서 빠지므로
        # last_seen만으로는 판단할 수 없다).
        stmt = select(GameResultParticipant.player_name).distinct()
        return set((await self._session.execute(stmt)).scalars())

    async def list_placeholder_raw_names_with_last_seen(self) -> list[tuple[str, date]]:
        # "회원으로 연결되지 않았다"인지는 이 player_name으로 매칭되는 kind='member'
        # replay_aliases 행이 있는지로 판단한다(더 이상 member_pk 컬럼이 없다) — 컴퓨터/
        # 비회원으로 이미 분류된 이름과, 아직 아무 분류도 없는 이름을 모두 포함한다(서비스
        # 레이어의 list_replay_name_mappings가 이 중 "이미 분류된" 것들은 last_seen만
        # 가져다 쓰고, "아직 미분류"인 것만 새 entry로 만든다). 마지막으로 나온 경기
        # 날짜가 필요해 matches와 조인해 그룹별 최댓값을 구한다.
        stmt = (
            select(GameResultParticipant.player_name, func.max(GameResult.match_date))
            .join(GameResult, GameResult.id == GameResultParticipant.match_id)
            .where(
                ~exists(
                    select(1).where(
                        ReplayAlias.raw_name == GameResultParticipant.player_name,
                        ReplayAlias.kind == "member",
                    )
                )
            )
            .group_by(GameResultParticipant.player_name)
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

