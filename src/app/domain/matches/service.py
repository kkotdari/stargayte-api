import base64
import calendar
import io
import zipfile
from datetime import UTC, date, datetime, timedelta, timezone
from functools import cmp_to_key

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.domain.matches.models import Match, MatchAttachment, MatchParticipant, MatchResult
from app.domain.matches.repository import MatchRepository
from app.domain.matches.schemas import (
    COMPUTER_ID_PREFIX,
    MANUAL_COMPUTER_RAW_NAME,
    MANUAL_UNREGISTERED_RAW_NAME,
    UNREGISTERED_ID_PREFIX,
    MatchAttachmentPayload,
    MatchAuthor,
    MatchOut,
    MatchSlot,
    MatchWrite,
    MemberStatsEntry,
    MemberStatsMonthEntry,
    RaceStatsEntry,
    TeamRankEntry,
    TeamRankingResponse,
    TeamRankMonthEntry,
    is_computer_slot,
    is_placeholder_slot,
    is_unregistered_slot,
)
from app.domain.members.models import Member, ReplayAlias
from app.domain.members.repository import MemberRepository
from app.storage.base import FileStorage
from app.storage.data_url import decode_data_url, guess_extension, is_data_url

# 실제 경기결과에 저장되는 종족(슬롯 등록 시 "랜덤"은 막혀 있다) — 종족별 통계 병기 기준.
BASE_RACES = ("테란", "프로토스", "저그")

# 유효APM/유효커맨드 이상치 제외 — 한 회원의 여러 경기 중 그 회원의 다른 경기들과 편차가
# 너무 심한 경기(리플레이 파싱 오류, 접속 종료 직전 렉 등으로 튀는 값)를 그 항목 평균에서만
# 뺀다. 표본이 너무 적으면(_OUTLIER_MIN_SAMPLES 미만) 뭐가 "편차가 심한지" 판단할 근거가
# 부족해 왜곡 위험이 크므로 그대로 둔다.
#
# 평균/표준편차가 아니라 중앙값(median)/MAD(중앙값 절대편차)로 이상치를 판단한다 — 평균과
# 표준편차는 이상치 값 자신이 계산에 끼어들어가 둘 다 함께 끌어올려버려서, 표본이 적을 때
# (기준선인 5~6경기) 그 이상치 스스로가 "평균에서 표준편차 2배 이내"를 통과해 버젓이 살아남는
# 문제(마스킹 효과)가 있었다. 중앙값과 MAD는 이상치 한두 개로는 거의 흔들리지 않아 표본이
# 적어도 안정적으로 잡아낸다.
_OUTLIER_MIN_SAMPLES = 5
_OUTLIER_Z = 2.0
# 정규분포를 가정할 때 표준편차 1에 대응하는 MAD 값의 역수(1/Φ⁻¹(0.75) ≈ 1.4826) — MAD에
# 곱해서 "이 분포가 정규분포였다면 표준편차가 이 정도였을" 스케일로 맞춰주면, 기존에 쓰던
# _OUTLIER_Z(2배) 기준값을 그대로 재사용할 수 있다.
_MAD_TO_STDEV = 1.4826


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 == 1 else (s[mid - 1] + s[mid]) / 2


def _outlier_keep_mask(values: list[float]) -> list[bool]:
    """values와 같은 길이의 bool 목록 — 중앙값에서 (표준편차 스케일로 환산한) MAD의
    _OUTLIER_Z배를 넘게 벗어난 값만 False. 표본 부족/MAD 0(값이 거의 다 같음)/전부 이상치로
    잡히는(방어적) 경우는 전부 True로 그대로 둔다."""
    n = len(values)
    if n < _OUTLIER_MIN_SAMPLES:
        return [True] * n
    med = _median(values)
    mad = _median([abs(v - med) for v in values]) * _MAD_TO_STDEV
    if mad == 0:
        return [True] * n
    mask = [abs(v - med) <= _OUTLIER_Z * mad for v in values]
    return mask if any(mask) else [True] * n


def _trimmed_avg_eapm(rows: list) -> int | None:
    values = [float(r.eapm) for r in rows if r.eapm is not None]
    if not values:
        return None
    mask = _outlier_keep_mask(values)
    kept = [v for v, keep in zip(values, mask) if keep]
    return round(sum(kept) / len(kept))


def _trimmed_avg_ecmd(rows: list) -> int | None:
    # 유효커맨드는 총합이 아니라 "분당" 값 — 이상치 판단은 경기별 분당 값(rate)을 기준으로
    # 하되, 실제 평균은 (원래 방식과 동일하게) 살아남은 경기들의 커맨드수 합계 / 시간(분)
    # 합계로 낸다. rate를 단순 평균하면 짧은 경기가 과대 대표돼 불공정해진다.
    games = [
        (r.effective_cmd_count, r.duration_seconds) for r in rows
        if r.effective_cmd_count is not None and r.duration_seconds
    ]
    if not games:
        return None
    rates = [cmd / (dur / 60) for cmd, dur in games]
    mask = _outlier_keep_mask(rates)
    kept_cmd_sum = sum(cmd for (cmd, _dur), keep in zip(games, mask) if keep)
    kept_dur_sum = sum(dur for (_cmd, dur), keep in zip(games, mask) if keep)
    return round(kept_cmd_sum / (kept_dur_sum / 60)) if kept_dur_sum else None


def _split_terms(query: str | None) -> list[str]:
    if not query:
        return []
    return query.split()


def _encode_cursor(match_no: str) -> str:
    return base64.urlsafe_b64encode(match_no.encode()).decode()


def _decode_cursor(cursor: str) -> str:
    try:
        return base64.urlsafe_b64decode(cursor.encode()).decode()
    except (ValueError, UnicodeDecodeError) as e:
        raise ValidationError("잘못된 커서입니다.") from e


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _month_range(month: str) -> tuple[date, date]:
    """"YYYY-MM"을 그 달의 첫날/마지막날로 바꾼다 — 랭킹 화면의 월 기준 기본 집계와
    월별 순위변동 비교(최근 5개월)가 함께 쓴다."""
    y, m = (int(p) for p in month.split("-"))
    last_day = calendar.monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, last_day)


_KST = timezone(timedelta(hours=9))


def _match_no_base(match_date: date, game_started_at: datetime | None) -> str:
    # 리플레이가 있으면 실제 경기 시작 시각(KST)을, 없으면(수동 등록) 경기 날짜만 알 수
    # 있으니 자정(000000)으로 채운다 — 같은 날 여러 건이면 뒤 2자리 일련번호로 갈린다.
    #
    # 수기등록은 실제 경기 시각을 몰라도 "제N경기" 순서(gameStartedAt 비교, MatchList.tsx의
    # compareByPlayOrder)를 매길 기준값이 필요해서, 프론트가 신규 등록 시점의 "지금"을
    # gameStartedAt에 채워 넣는다(서비스 다른 곳 참고) — 그 값은 사용자가 고른 경기
    # 날짜(match_date)와 전혀 무관한 "등록한 시각"일 뿐이라 match_no에 그대로 쓰면 안 된다
    # (실제로 지적받은 문제 — 4월 1일자로 등록한 경기의 match_no가 등록한 날(오늘)로 붙음).
    # 리플레이로 파싱된 진짜 시각은 항상 match_date와 같은 날짜이므로(그 시각으로부터
    # match_date 자체를 계산해서 채운다), 날짜가 어긋나면 신뢰할 수 없는 값(수기등록의
    # "지금")으로 보고 자정 기준으로 대체한다.
    if game_started_at is not None:
        local = game_started_at.astimezone(_KST) if game_started_at.tzinfo else game_started_at
        if local.date() == match_date:
            return local.strftime("%y%m%d%H%M%S")
    return match_date.strftime("%y%m%d") + "000000"


def _to_utc_naive(dt: datetime) -> datetime:
    # Postgres(timestamptz)는 aware로, SQLite는 tz 정보 없이 naive로 돌아오는 등 방언마다
    # 달라서, 비교 전에 항상 "UTC 기준 naive"로 맞춘다(입력값은 항상 UTC로 정규화해서 만듦).
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


class _RaceAgg:
    """aggregate_stats가 돌려주는 (member_pk, race) 단위 원본 행 하나 또는 여러 개를
    합산해서 RaceStatsEntry로 만드는 중간 누산기."""

    __slots__ = (
        "plays", "wins", "draws",
        "apm_sum", "apm_cnt", "eapm_sum", "eapm_cnt",
        "cmd_sum", "cmd_cnt", "ecmd_sum", "ecmd_duration_sum",
    )

    def __init__(self) -> None:
        self.plays = 0
        self.wins = 0
        self.draws = 0
        self.apm_sum = 0
        self.apm_cnt = 0
        self.eapm_sum = 0
        self.eapm_cnt = 0
        self.cmd_sum = 0
        self.cmd_cnt = 0
        self.ecmd_sum = 0
        self.ecmd_duration_sum = 0

    def add_row(self, row) -> None:
        self.plays += row.plays
        self.wins += row.wins
        self.draws += row.draws
        self.apm_sum += row.apm_sum
        self.apm_cnt += row.apm_cnt
        self.eapm_sum += row.eapm_sum
        self.eapm_cnt += row.eapm_cnt
        self.cmd_sum += row.cmd_sum
        self.cmd_cnt += row.cmd_cnt
        self.ecmd_sum += row.ecmd_sum
        self.ecmd_duration_sum += row.ecmd_duration_sum

    def to_entry(self) -> RaceStatsEntry:
        losses = self.plays - self.wins - self.draws
        win_rate = round((self.wins / self.plays) * 1000) / 10 if self.plays else 0.0
        # 유효커맨드는 총합의 평균이 아니라 "분당" 값 — 경기 길이가 제각각이라 총합만
        # 평균 내면 긴 경기를 많이 한 사람이 불리하게(혹은 유리하게) 왜곡된다.
        avg_ecmd = (
            round(self.ecmd_sum / (self.ecmd_duration_sum / 60)) if self.ecmd_duration_sum else None
        )
        return RaceStatsEntry(
            plays=self.plays,
            wins=self.wins,
            losses=losses,
            draws=self.draws,
            win_rate=win_rate,
            avg_apm=round(self.apm_sum / self.apm_cnt) if self.apm_cnt else None,
            avg_eapm=round(self.eapm_sum / self.eapm_cnt) if self.eapm_cnt else None,
            avg_cmd=round(self.cmd_sum / self.cmd_cnt) if self.cmd_cnt else None,
            avg_ecmd=avg_ecmd,
        )


class _Record:
    """한 방향 전적(내가 상대에게) — 승점은 승 +1, 무 0, 패 -1."""

    __slots__ = ("plays", "wins", "draws")

    def __init__(self, plays: int, wins: int, draws: int) -> None:
        self.plays = plays
        self.wins = wins
        self.draws = draws

    @property
    def points(self) -> int:
        losses = self.plays - self.wins - self.draws
        return self.wins - losses


# member_pk -> 상대 member_pk -> 그 상대에게의 전적
HeadToHead = dict[int, dict[int, _Record]]


def _points_against(h2h: HeadToHead, pk: int, opponents: set[int]) -> int:
    """pk가 opponents 전체를 상대로 딴 승점 합 — 한 번도 안 붙어본 상대는 0점으로 친다
    (붙어본 적 없는 상대는 애초에 opponents에 들어오지 않으므로 실제로는 건너뛰기만 한다)."""
    row = h2h.get(pk, {})
    return sum(row[opp].points for opp in opponents if opp in row)


# 팀으로 인정하는 최소 인원 — 2명 이상이면 (2:2든 3:3이든) 그 팀 구성 그대로 하나의 팀이다.
TEAM_MIN_SIZE = 2


def _to_match_slot(p: MatchParticipant, alias_by_player_name: dict[str, ReplayAlias]) -> MatchSlot:
    # 회원인지, 아니면 컴퓨터(AI)/비회원 참가자인지는 더 이상 member_pk 컬럼이 아니라
    # player_name → replay_aliases 조회로 판단한다(alias_by_player_name, 라우터에서
    # 한 번만 가져와 여러 경기를 직렬화하는 동안 재사용 — list_all_replay_aliases는
    # ReplayAlias.member까지 eager load 되어 있다). 회원이 아니면 실제로 저장된 고유
    # 아이디가 없으니 team 내 position으로 매 조회마다 안정적으로 재생성한다(같은 경기를
    # 다시 읽어도 동일한 값). 컴퓨터/비회원 중 어느 쪽인지는 alias.kind == "computer"면
    # 컴퓨터로 취급한다. 수기등록 슬롯의 예약 player_name은 마이그레이션이 replay_aliases에
    # 심어둔 행에 기대지 않고 여기서 직접 비교한다 — alias 조회 결과와 무관하게 항상
    # 정확해야 하는 값이라(테스트 DB처럼 그 시드 데이터가 없는 환경도 포함). 분류가 없으면
    # 비회원으로 본다 — 컴퓨터는 등록 시점에 항상 kind="computer"로 기억되므로
    # (_remember_placeholder_raw_names), 조회가 안 되는 이름은 "아직 아무도 분류하지 않은
    # 사람"이라는 뜻이다. 예전엔 반대로 컴퓨터를 기본값으로 뒀는데, 그러면 비회원을
    # 기억시키려고 매번 alias를 만들어야 했고 그 탓에 그 이름을 회원으로 연결할 기회가
    # 사라졌다.
    alias = alias_by_player_name.get(p.player_name)
    if alias is not None and alias.kind == "member":
        member_id = alias.member.id
    elif p.player_name == MANUAL_COMPUTER_RAW_NAME or (alias is not None and alias.kind == "computer"):
        member_id = f"{COMPUTER_ID_PREFIX}{p.position}"
    else:
        member_id = f"{UNREGISTERED_ID_PREFIX}{p.position}"
    return MatchSlot(
        member_id=member_id,
        race=p.race,
        player_name=p.player_name,
        apm=p.apm,
        eapm=p.eapm,
        cmd_count=p.cmd_count,
        effective_cmd_count=p.effective_cmd_count,
    )


def to_match_out(match: Match, storage: FileStorage, alias_by_player_name: dict[str, ReplayAlias]) -> MatchOut:
    team1 = [_to_match_slot(p, alias_by_player_name) for p in match.participants if p.team == "team1"]
    team2 = [_to_match_slot(p, alias_by_player_name) for p in match.participants if p.team == "team2"]
    attachment = None
    if match.attachment is not None:
        attachment = MatchAttachmentPayload(
            name=match.attachment.file_name, url=storage.url_for(match.attachment.file_path)
        )
    author = None
    if match.creator is not None:
        author = MatchAuthor(id=match.creator.id, nickname=match.creator.nickname)
    # 공식경기 예약(scheduled, 결과 없이 등록) 기능이 없어진 뒤로는 모든 경기가 등록과
    # 동시에 결과를 함께 저장하므로 result_row가 항상 존재한다.
    result_row = match.result_row
    assert result_row is not None, "모든 경기는 result_row를 가져야 합니다."
    return MatchOut(
        id=match.id,
        match_no=match.match_no,
        date=match.match_date.isoformat(),
        team1=team1,
        team2=team2,
        result=result_row.result,
        match_type=match.match_type,
        note=match.note,
        attachment=attachment,
        created_by=author,
        map_name=result_row.map_name,
        game_started_at=result_row.game_started_at,
        duration_seconds=result_row.duration_seconds,
    )


class MatchService:
    def __init__(self, session: AsyncSession, storage: FileStorage) -> None:
        self._session = session
        self._repo = MatchRepository(session)
        self._member_repo = MemberRepository(session)
        self._storage = storage

    async def list_matches_page(
        self,
        *,
        cursor: str | None,
        limit: int,
        sort: str,
        date_from: str | None,
        date_to: str | None,
        match_type: str | None,
        user_query: str | None,
        match_all_users: bool,
        has_placeholder: bool = False,
        match_no: str | None = None,
        team_member_ids: list[str] | None = None,
    ) -> tuple[list[Match], str | None, bool]:
        decoded_cursor = _decode_cursor(cursor) if cursor else None
        matches, has_more = await self._repo.list_page(
            cursor=decoded_cursor,
            limit=limit,
            sort=sort,
            date_from=_parse_date(date_from),
            date_to=_parse_date(date_to),
            match_type=match_type,
            terms=_split_terms(user_query),
            match_all_terms=match_all_users,
            has_placeholder=has_placeholder,
            match_no=match_no,
            team_member_pks=await self._team_member_pks(team_member_ids),
        )
        next_cursor = _encode_cursor(matches[-1].match_no) if has_more and matches else None
        return matches, next_cursor, has_more

    async def _team_member_pks(self, team_member_ids: list[str] | None) -> list[int] | None:
        """팀 랭킹에서 넘어온 로그인 아이디들을 pk로 바꾼다 — 하나라도 없는 회원이 섞여 있으면
        그 팀 자체가 성립하지 않으므로, 아무 경기도 안 걸리도록 존재하지 않는 pk를 하나 남긴다
        (조건을 통째로 무시해서 전체 경기를 보여주는 것보다 이쪽이 안전하다)."""
        if not team_member_ids:
            return None
        pks: list[int] = []
        for login_id in team_member_ids:
            member = await self._member_repo.get_by_login_id(login_id)
            if member is None:
                return [-1]
            pks.append(member.pk)
        return pks

    async def count_matches(
        self,
        *,
        date_from: str | None,
        date_to: str | None,
        match_type: str | None,
        user_query: str | None,
        match_all_users: bool,
        has_placeholder: bool = False,
        match_no: str | None = None,
        team_member_ids: list[str] | None = None,
    ) -> int:
        """무한스크롤로 화면엔 일부만 로드돼도, list_matches_page와 같은 필터 조건에
        해당하는 전체 건수를 알려주기 위한 조회(커서/limit 없음)."""
        return await self._repo.count_page(
            date_from=_parse_date(date_from),
            date_to=_parse_date(date_to),
            match_type=match_type,
            terms=_split_terms(user_query),
            match_all_terms=match_all_users,
            has_placeholder=has_placeholder,
            match_no=match_no,
            team_member_pks=await self._team_member_pks(team_member_ids),
        )

    async def get_stats(
        self,
        *,
        member_ids: list[str] | None,
        date_from: str | None,
        date_to: str | None,
        match_type: str | None,
        race: str | None,
    ) -> list[MemberStatsEntry]:
        if member_ids is not None:
            members = []
            for login_id in member_ids:
                member = await self._member_repo.get_by_login_id(login_id)
                if member is not None:
                    members.append(member)
        else:
            members = await self._member_repo.list_all()
        if not members:
            return []

        parsed_date_from = _parse_date(date_from)
        parsed_date_to = _parse_date(date_to)
        rows = await self._repo.aggregate_stats(
            member_pks=[m.pk for m in members],
            date_from=parsed_date_from,
            date_to=parsed_date_to,
            match_type=match_type,
        )
        by_member: dict[int, dict[str, object]] = {}
        for row in rows:
            by_member.setdefault(row.member_pk, {})[row.race] = row

        # 유효APM/유효커맨드는 합계만으로는 이상치(그 회원의 다른 경기들과 편차가 너무 심한
        # 경기 하나)를 가려낼 수 없어, 경기 단위 원본을 따로 받아 회원+종족별로 묶어둔다.
        raw_rows = await self._repo.raw_eapm_ecmd_rows(
            member_pks=[m.pk for m in members],
            date_from=parsed_date_from,
            date_to=parsed_date_to,
            match_type=match_type,
        )
        raw_by_member_race: dict[int, dict[str, list]] = {}
        for raw in raw_rows:
            raw_by_member_race.setdefault(raw.member_pk, {}).setdefault(raw.race, []).append(raw)

        entries: list[MemberStatsEntry] = []
        for member in members:
            race_rows = by_member.get(member.pk, {})
            raw_race_rows = raw_by_member_race.get(member.pk, {})

            by_race: dict[str, RaceStatsEntry] = {}
            for r in BASE_RACES:
                agg = _RaceAgg()
                if r in race_rows:
                    agg.add_row(race_rows[r])
                entry = agg.to_entry()
                raw_for_race = raw_race_rows.get(r, [])
                by_race[r] = entry.model_copy(update={
                    "avg_eapm": _trimmed_avg_eapm(raw_for_race),
                    "avg_ecmd": _trimmed_avg_ecmd(raw_for_race),
                })

            overall_agg = _RaceAgg()
            if race and race != "all":
                if race in race_rows:
                    overall_agg.add_row(race_rows[race])
                overall_raw = raw_race_rows.get(race, [])
            else:
                for row in race_rows.values():
                    overall_agg.add_row(row)
                overall_raw = [raw for rows_for_race in raw_race_rows.values() for raw in rows_for_race]

            # 종족 필터와 무관하게 항상 실제 참가 기록 기준 최다 종족 — 동률이면 테란→프로토스→
            # 저그 고정 순서로 결정한다(사전순 등 우연에 맡기지 않기 위해).
            most_played_race = None
            best_plays = 0
            for r in BASE_RACES:
                plays = race_rows[r].plays if r in race_rows else 0
                if plays > best_plays:
                    best_plays = plays
                    most_played_race = r

            overall_entry = overall_agg.to_entry().model_copy(update={
                "avg_eapm": _trimmed_avg_eapm(overall_raw),
                "avg_ecmd": _trimmed_avg_ecmd(overall_raw),
            })
            entries.append(
                MemberStatsEntry(
                    member_id=member.id,
                    overall=overall_entry,
                    by_race=by_race,
                    most_played_race=most_played_race,
                )
            )

        await self._apply_rank_order(
            entries,
            members,
            date_from=parsed_date_from,
            date_to=parsed_date_to,
            match_type=match_type,
            race=race,
        )
        return entries

    async def _apply_rank_order(
        self,
        entries: list[MemberStatsEntry],
        members: list[Member],
        *,
        date_from: date | None,
        date_to: date | None,
        match_type: str | None,
        race: str | None,
    ) -> None:
        """랭킹 정렬(sort_order/tie_group)을 entries에 채워 넣는다 — entries[i]는 members[i]의 것이다.

        승률도, 승점(승-패)도 기준이 아니다. 둘을 견줄 때 다음 순서로만 가른다.

          ① 승자승 — 그 둘이 직접 붙은 전적. 이긴 쪽이 무조건 위다(1전 1승도 100전 99승을 이긴다).
          ② 간접비교 — 둘 다 붙어본 적 있는 "공통상대"에 대한 각자의 승점(승 +1, 무 0, 패 -1).
             공통상대가 하나도 없으면(0 대 0으로 같으니) 이 기준으로는 못 가르고 그대로 다음
             기준으로 넘어간다 — 비교 대상이 없다는 건 가를 근거가 없다는 뜻이지 우열이 있다는
             뜻이 아니다.
          ③ 전체 승수.

        여기까지 전부 같으면 진짜 동률이다 — 그 이하로는 절대 가르지 않는다(예전엔 여기에
        "승점" 기준이 하나 더 있었지만, 승-패만으로 매기는 승점은 사실상 승수와 거의 같은
        얘기를 두 번 하는 셈이라 없앴다).

        ①은 상대가 누구냐에 따라 값이 달라지는 쌍(pair) 비교라, 회원별 점수 하나로 미리 뽑아
        정렬 키로 쓸 수가 없다 — 그래서 비교 함수(cmp)로 정렬한다. 그 대가로 "A가 B를 이기고,
        B가 C를 이기고, C가 A를 이긴" 순환이 생기면 셋 사이의 순서는 원리적으로 정할 수 없다.
        그때는 아래 ②~③이 대신 가르고, 그마저 같으면(=진짜 동률) 정렬 자체는 끝나야 하니
        로그인 아이디로 순서만 고정한다(같은 입력이면 항상 같은 결과가 나오게). 다만 이
        아이디 비교는 정렬 안정성만을 위한 내부 장치일 뿐 "공동순위인지"에는 반영하지
        않는다 — tie_group은 ①~③만으로 판단한다(_rank_key 참고).

        여기서만 정렬을 하고 entries 자체의 순서(=회원 목록 순서)는 바꾸지 않는다 — 이 응답은
        랭킹 말고 전적통계/상세 모달도 함께 쓰기 때문이다."""
        rankable = [(e, m) for e, m in zip(entries, members) if e.overall.plays > 0]
        if len(rankable) < 2:
            if rankable:
                rankable[0][0].sort_order = 0
                rankable[0][0].tie_group = 0
            return

        # 승자승이 1순위라 맞대결 전적은 항상 필요하다(예전엔 승률 동률일 때만 조회했다).
        rows = await self._repo.head_to_head_rows(
            member_pks=[m.pk for _, m in rankable],
            date_from=date_from,
            date_to=date_to,
            match_type=match_type,
            race=race,
        )
        h2h: HeadToHead = {}
        for row in rows:
            h2h.setdefault(row.member_pk, {})[row.opponent_pk] = _Record(
                plays=row.plays, wins=row.wins, draws=row.draws,
            )

        def _rank_key(a_entry: MemberStatsEntry, a_member: Member, b_entry: MemberStatsEntry, b_member: Member) -> int:
            """①~③ 세 기준만으로 가른다 — 0이면 그 이하로는 절대 안 가르는 진짜 동률."""
            # ① 승자승 — 서로 붙은 전적만 본다(안 붙었으면 0이라 다음 기준으로 넘어간다).
            head = _points_against(h2h, a_member.pk, {b_member.pk})
            if head != 0:
                return -1 if head > 0 else 1

            # ② 간접비교 — 둘 다 붙어본 적 있는 상대에 대한 각자의 승점. 서로는 ①에서 이미
            # 봤으니 상대 후보에서 뺀다. 공통상대가 하나도 없으면 둘 다 0점이라 그대로
            # 다음 기준으로 넘어간다(가를 근거가 없는 것이지 우열이 있는 게 아니다).
            common = (set(h2h.get(a_member.pk, {})) & set(h2h.get(b_member.pk, {}))) - {a_member.pk, b_member.pk}
            a_common = _points_against(h2h, a_member.pk, common)
            b_common = _points_against(h2h, b_member.pk, common)
            if a_common != b_common:
                return -1 if a_common > b_common else 1

            # ③ 전체 승수.
            if a_entry.overall.wins != b_entry.overall.wins:
                return -1 if a_entry.overall.wins > b_entry.overall.wins else 1
            return 0

        def _compare(a: tuple, b: tuple) -> int:
            (a_entry, a_member), (b_entry, b_member) = a, b
            ranked = _rank_key(a_entry, a_member, b_entry, b_member)
            if ranked != 0:
                return ranked
            # ①~③까지 전부 같으면(진짜 동률) 정렬 자체는 끝나야 하니 로그인 아이디로
            # 순서만 고정한다 — 이건 정렬 안정성을 위한 내부 장치일 뿐 "공동순위인지"에는
            # 반영하지 않는다(tie_group 계산은 아래에서 _rank_key만 다시 쓴다).
            return -1 if a_member.id < b_member.id else (1 if a_member.id > b_member.id else 0)

        # 순환이 있을 때도 매 요청 같은 결과가 나오도록 먼저 아이디로 고정해두고 정렬한다.
        rankable.sort(key=lambda x: x[1].id)
        rankable.sort(key=cmp_to_key(_compare))

        # 공동순위는 "①~③ 세 기준이 모두 같아 _rank_key가 0을 돌려준 사이"다 — _compare의
        # 로그인 아이디 fallback까지 같이 쓰면 서로 다른 두 회원의 아이디 문자열은 절대
        # 같을 수 없어 tie_group이 사실상 sort_order와 항상 같아지는(=아무도 공동순위가
        # 안 되는) 버그가 생긴다(실제로 있었던 문제) — 그래서 여기서는 _rank_key만 쓴다.
        tie_group = 0
        for i, (entry, member) in enumerate(rankable):
            if i > 0:
                prev_entry, prev_member = rankable[i - 1]
                if _rank_key(prev_entry, prev_member, entry, member) != 0:
                    tie_group = i
            entry.sort_order = i
            entry.tie_group = tie_group

    async def get_main_race(
        self,
        *,
        member_id: str,
        date_from: str | None,
        date_to: str | None,
        match_type: str | None,
    ) -> str | None:
        entries = await self.get_stats(
            member_ids=[member_id],
            date_from=date_from,
            date_to=date_to,
            match_type=match_type,
            race=None,
        )
        return entries[0].most_played_race if entries else None

    async def get_stats_monthly(
        self,
        *,
        months: list[str],
        member_ids: list[str] | None,
        match_type: str | None,
        race: str | None,
    ) -> list[MemberStatsMonthEntry]:
        """개인 랭킹의 월별 순위변동(최근 5개월) 모달과, 목록의 전월 대비 화살표가 함께
        쓴다 — 달마다 왕복하는 대신 한 번에 여러 달을 모아 받는다(요청: "api로 랭킹 목록
        가져올때 배열형태로 파라미터 추가"). 달마다 완전히 독립된 get_stats 호출이라(그
        달만의 기간으로 순위를 다시 매김) 여기서 합칠 계산은 없다."""
        results: list[MemberStatsMonthEntry] = []
        for month in months:
            date_from, date_to = _month_range(month)
            entries = await self.get_stats(
                member_ids=member_ids,
                date_from=date_from.isoformat(),
                date_to=date_to.isoformat(),
                match_type=match_type,
                race=race,
            )
            results.append(MemberStatsMonthEntry(month=month, members=entries))
        return results

    async def get_team_ranking(
        self, *, date_from: date | None = None, date_to: date | None = None,
    ) -> TeamRankingResponse:
        """실제로 함께 뛴 팀 구성(2인 이상)마다의 승점 랭킹 — date_from/date_to를 안 넘기면
        전체 기간이 대상이고(예전 동작 그대로), 랭킹 화면이 기본으로 쓰는 "이번 달" 집계나
        월별 순위변동 비교(get_team_ranking_monthly)는 이 값을 채워 특정 달로 좁힌다.

        팀의 정체성은 "그 경기에서 같은 편이었던 회원들의 집합" 하나뿐이다 — 순서도, 어느
        경기였는지도 상관없어서 [A,B]는 늘 같은 팀으로 누적된다. 실제 팀 구성만 잡고 부분
        조합([A,B,C]에서 [A,B])은 따로 세지 않는다 — 3:3에서 뽑아낸 2인 조합은 그 둘이 실제로
        2:2를 뛴 적이 없는데도 2인 팀 랭킹에 섞여 들어가기 때문이다.

        정렬은 승점(승 +1, 무 0, 패 -1) → 승수 → 경기수 순. 승점은 음수가 될 수 있고, 개인전
        랭킹과 달리 승자승(맞대결)은 보지 않는다. 인원수(2인/3인/4인)별로 따로 줄세우는 건
        화면(프론트)의 몫이다 — member_ids 길이만 봐도 인원수를 알 수 있어 서버가 다시 나눠
        줄 필요가 없다."""
        rows = await self._repo.team_participant_rows(date_from=date_from, date_to=date_to)

        # (경기, 팀) 한 칸에 그 편으로 뛴 슬롯을 전부 모은다(컴퓨터/비회원은 member_pk가
        # None) — 같은 경기의 team1/team2가 각각 한 칸이고, 그 칸의 승패는 경기 결과
        # 하나로 결정된다.
        sides: dict[tuple[int, str], list[int | None]] = {}
        result_of: dict[int, str] = {}
        for row in rows:
            sides.setdefault((row.match_id, row.team), []).append(row.member_pk)
            result_of[row.match_id] = row.result

        # 화면의 2×2 격자를 채울 구성원 순서 기준 — 같은 승점 규칙으로 매긴 개인 승점
        # (1:1 경기까지 전부 포함한 그 사람의 전체 성적이다).
        member_points: dict[int, int] = {}
        teams: dict[tuple[int, ...], dict[str, int]] = {}
        for (match_id, team), slot_pks in sides.items():
            result = result_of[match_id]
            point = 0 if result == "draw" else (1 if result == team else -1)
            member_pks = [pk for pk in slot_pks if pk is not None]
            for pk in member_pks:
                member_points[pk] = member_points.get(pk, 0) + point
            # 이 편에 컴퓨터/비회원이 한 명이라도 섞여 있으면(slot 수와 실제 회원 수가
            # 다르면) 남은 실제 회원끼리를 별개의(더 작은) 팀으로 잘못 집계하지 않도록
            # 통째로 건너뛴다 — 예: 3:3에 컴퓨터 1명이 끼면 실제 회원은 2명뿐이라 2인
            # 팀처럼 보이지만, 그 둘이 실제로 2:2를 뛴 적은 없다(실제로 지적받은 문제).
            has_placeholder = len(member_pks) != len(slot_pks)
            if has_placeholder or len(member_pks) < TEAM_MIN_SIZE:
                continue
            agg = teams.setdefault(tuple(sorted(member_pks)), {"plays": 0, "wins": 0, "draws": 0, "points": 0})
            agg["plays"] += 1
            agg["points"] += point
            if point > 0:
                agg["wins"] += 1
            elif point == 0:
                agg["draws"] += 1

        if not teams:
            return TeamRankingResponse(teams=[])

        member_by_pk = {m.pk: m for m in await self._member_repo.list_all()}

        entries: list[TeamRankEntry] = []
        for pks, agg in teams.items():
            # 승점 높은 순 → (같으면) 닉네임 순. 순서만 정하는 값이라 완전 동률이어도 매 요청
            # 같은 결과가 나오도록 닉네임까지 본다.
            ordered_pks = sorted(pks, key=lambda pk: (-member_points.get(pk, 0), member_by_pk[pk].nickname))
            entries.append(
                TeamRankEntry(
                    member_ids=[member_by_pk[pk].id for pk in ordered_pks],
                    plays=agg["plays"],
                    wins=agg["wins"],
                    losses=agg["plays"] - agg["wins"] - agg["draws"],
                    draws=agg["draws"],
                    points=agg["points"],
                )
            )
        entries.sort(key=lambda e: (-e.points, -e.wins, -e.plays, e.member_ids))

        return TeamRankingResponse(teams=entries)

    async def get_team_ranking_monthly(self, *, months: list[str]) -> list[TeamRankMonthEntry]:
        """팀 랭킹의 월별 순위변동(최근 5개월) 모달과, 목록의 전월 대비 화살표가 함께
        쓴다 — get_stats_monthly와 같은 이유로 한 번에 여러 달을 모아 받는다. 인원수
        (2인/3인/4인)별로 다시 줄세우는 건 화면(프론트)의 몫이라 여기서는 달마다 그 달
        전체 팀(모든 인원수 섞여서)을 그대로 돌려준다."""
        results: list[TeamRankMonthEntry] = []
        for month in months:
            date_from, date_to = _month_range(month)
            resp = await self.get_team_ranking(date_from=date_from, date_to=date_to)
            results.append(TeamRankMonthEntry(month=month, teams=resp.teams))
        return results

    async def get_earliest_match_date(self) -> str | None:
        d = await self._repo.earliest_match_date()
        return d.isoformat() if d else None

    async def check_duplicates(self, game_started_at: list[str]) -> list[str]:
        candidates: dict[datetime, str] = {}
        for raw in game_started_at:
            try:
                candidates[_to_utc_naive(datetime.fromisoformat(raw.replace("Z", "+00:00")))] = raw
            except ValueError:
                continue
        if not candidates:
            return []
        existing = {_to_utc_naive(dt) for dt in await self._repo.list_game_started_ats()}
        return [raw for dt, raw in candidates.items() if dt in existing]

    async def lookup_replay_name_classifications(self, raw_names: list[str]) -> list[ReplayAlias]:
        return await self._repo.list_replay_name_classifications(raw_names)

    async def set_replay_name_classification(self, raw_name: str, kind: str) -> ReplayAlias:
        existing = await self._repo.get_replay_name_classification(raw_name)
        if existing is not None:
            existing.kind = kind
            await self._session.commit()
            return existing
        entry = ReplayAlias(raw_name=raw_name, kind=kind)
        self._repo.add_replay_name_classification(entry)
        await self._session.commit()
        await self._session.refresh(entry)
        return entry

    async def list_replay_name_mappings(self) -> list[dict]:
        """유저 매핑 관리 화면 — 리플레이 원본 이름(rawName) 하나를 기준으로, replay_aliases
        (회원 별칭/컴퓨터·비회원 분류)와 아직 그 어느 쪽도 아닌 미해결(match_participants에만
        남아있는) 항목을 합쳐서 중복 없이 보여준다. raw_name이 replay_aliases 안에서 유일하므로
        회원/분류가 겹칠 일은 원천적으로 없다. 수기등록 컴퓨터/비회원 전용 예약 raw_name
        (MANUAL_COMPUTER_RAW_NAME/MANUAL_UNREGISTERED_RAW_NAME)은 실제 게임 아이디가
        아니라 내부 구현용 시스템 값이라 이 화면에는 아예 보이지 않는다."""
        reserved_raw_names = (MANUAL_COMPUTER_RAW_NAME, MANUAL_UNREGISTERED_RAW_NAME)
        aliases = await self._repo.list_all_replay_aliases()
        placeholder_rows = await self._repo.list_placeholder_raw_names_with_last_seen()
        last_seen_by_raw_name = dict(placeholder_rows)

        entries: dict[str, dict] = {
            a.raw_name: {
                "raw_name": a.raw_name, "kind": a.kind, "member": a.member,
                "last_seen": last_seen_by_raw_name.get(a.raw_name),
            }
            for a in aliases
            if a.raw_name not in reserved_raw_names
        }
        for raw_name, last_seen in placeholder_rows:
            if raw_name in reserved_raw_names:
                continue
            entries.setdefault(
                raw_name,
                {"raw_name": raw_name, "kind": "unresolved", "member": None, "last_seen": last_seen},
            )

        # 미해결(아직 아무 것도 연결 안 된) 항목을 맨 위에, 그 안에서는 최근에 나온 순으로 —
        # 운영자가 당장 처리해야 할 것부터 보이게 한다. 나머지(이미 연결된 것들)는 그 아래
        # 이름순으로 이어붙인다.
        unresolved = sorted(
            (e for e in entries.values() if e["kind"] == "unresolved"),
            key=lambda e: e["last_seen"] or date.min, reverse=True,
        )
        resolved = sorted(
            (e for e in entries.values() if e["kind"] != "unresolved"),
            key=lambda e: e["raw_name"],
        )
        return unresolved + resolved

    async def set_replay_name_mapping(
        self, raw_name: str, kind: str, member_id: str | None, *, actor_pk: int
    ) -> dict:
        if raw_name in (MANUAL_COMPUTER_RAW_NAME, MANUAL_UNREGISTERED_RAW_NAME):
            raise ValidationError("이 이름은 수정할 수 없는 시스템 예약값입니다.")
        # 새 매핑을 걸기 전에, 이 raw_name에 걸려 있던 예전 매핑(분류/다른 회원의 별칭)은
        # 항상 먼저 지운다 — 한 raw_name은 항상 하나의 대상만 가리켜야 목록에서 중복 없이
        # 보인다.
        await self._repo.delete_replay_alias(raw_name)

        member_out: Member | None = None
        if kind == "member":
            if not member_id:
                raise ValidationError("회원으로 연결하려면 회원을 선택해야 합니다.")
            member = await self._member_repo.get_by_login_id(member_id)
            if member is None:
                raise NotFoundError("회원을 찾을 수 없습니다.")
            member.replay_aliases.append(ReplayAlias(raw_name=raw_name, kind="member"))
            member.updated_by = actor_pk
            await self._repo.resolve_placeholder_raw_name_to_member(raw_name, member.pk)
            member_out = member
        elif kind in ("computer", "unregistered"):
            # slot_kind 컬럼이 없어진 뒤로는 이 alias 행 하나가 분류의 유일한 근거라,
            # match_participants 쪽엔 따로 업데이트할 게 없다(_to_match_slot이 조회 시점에
            # raw_name → kind를 그때그때 찾는다).
            entry = ReplayAlias(raw_name=raw_name, kind=kind)
            self._repo.add_replay_name_classification(entry)
        elif kind == "unresolved":
            # 회원으로 연결돼 있었다면 member_pk가 이미 채워져 있으니 다시 비워야
            # "미지정"으로 목록에 되돌아온다(위 revert_raw_name_to_unresolved 참고).
            await self._repo.revert_raw_name_to_unresolved(raw_name)
        else:
            raise ValidationError(f"알 수 없는 매핑 종류입니다: {kind}")

        await self._session.commit()
        return {"raw_name": raw_name, "kind": kind, "member": member_out}

    async def delete_replay_name_mapping(self, raw_name: str) -> None:
        """유저 매핑 관리 화면의 "삭제" — 매핑 데이터(replay_aliases 행) 자체를 지워
        목록에서 완전히 사라지게 한다. "미지정으로 되돌리기"(set_replay_name_mapping의
        kind="unresolved")와는 다르다 — 그쪽은 경기 기록이 남아있는 한 계속 목록에
        (미지정으로) 다시 나타나야 정상이고, 이쪽(삭제)은 그 경기 기록 자체가 없을 때만
        허용해 진짜로 없앨 수 있다."""
        if raw_name in (MANUAL_COMPUTER_RAW_NAME, MANUAL_UNREGISTERED_RAW_NAME):
            raise ValidationError("이 이름은 삭제할 수 없는 시스템 예약값입니다.")
        if await self._repo.raw_name_has_any_participants(raw_name):
            raise ValidationError("이 게임 아이디로 등록된 경기가 있어 삭제할 수 없어요 — 대신 미지정으로 되돌려 주세요.")
        await self._repo.delete_replay_alias(raw_name)
        await self._session.commit()

    async def get_match(self, match_id: int) -> Match:
        match = await self._repo.get(match_id)
        if match is None:
            raise NotFoundError("경기결과를 찾을 수 없습니다.")
        return match

    async def build_replay_archive(self) -> bytes:
        """등록된 모든 리플레이(.rep 첨부)를 zip 바이트로 묶는다(운영자 제어판의 '리플레이
        전체 다운로드'). 폴더 구분 없이 평평하게 담는다(요청). 파일이 유실된 건은 조용히
        건너뛰고, 파일명이 겹치면 " (2)"식으로 유일하게 만든다."""
        rows = await self._repo.list_all_attachments()
        used: set[str] = set()

        def unique(name: str) -> str:
            if name not in used:
                used.add(name)
                return name
            stem, dot, ext = name.rpartition(".")
            i = 2
            while True:
                cand = f"{stem} ({i}).{ext}" if dot else f"{name} ({i})"
                if cand not in used:
                    used.add(cand)
                    return cand
                i += 1

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for _match_date, _match_no, file_name, file_path in rows:
                try:
                    data = await self._storage.read(file_path)
                except Exception:
                    continue
                zf.writestr(unique(file_name), data)
        return buf.getvalue()

    async def alias_by_player_name(self) -> dict[str, ReplayAlias]:
        """to_match_out이 참가자의 회원/컴퓨터/비회원 여부를 판단할 때 쓰는 조회용 —
        라우터에서 한 번만 가져와 여러 경기를 직렬화하는 동안 재사용한다."""
        aliases = await self._repo.list_all_replay_aliases()
        return {a.raw_name: a for a in aliases}

    async def create_match(self, payload: MatchWrite, *, actor: Member) -> Match:
        await self._ensure_no_duplicate_members(payload)
        members_by_id = await self._ensure_members_exist(payload.team1 + payload.team2)
        await self._remember_placeholder_raw_names(payload)
        await self._ensure_player_name_classifications(payload.team1, payload.team2, members_by_id)

        match_date = date.fromisoformat(payload.date)
        match_no_base = _match_no_base(match_date, payload.game_started_at)
        match_no_suffix = await self._repo.next_match_no_suffix(match_no_base)

        # attachment=None 을 명시해 flush 이후 접근 시 비동기 lazy-load가 걸리지 않게 한다.
        match = Match(
            match_no=f"{match_no_base}{match_no_suffix:02d}",
            match_date=match_date,
            match_type=payload.match_type,
            note=payload.note,
            attachment=None,
            result_row=MatchResult(
                result=payload.result,
                map_name=payload.map_name,
                game_started_at=payload.game_started_at,
                duration_seconds=payload.duration_seconds,
            ),
            created_by=actor.pk,
            updated_by=actor.pk,
        )
        match.participants = self._build_participants(
            payload.team1, payload.team2, members_by_id, actor_pk=actor.pk
        )
        self._repo.add(match)
        await self._repo.flush()

        if payload.attachment is not None:
            await self._apply_attachment(match, payload.attachment, actor_pk=actor.pk)

        await self._session.commit()
        return await self._repo.refresh(match)

    async def update_match(self, match_id: int, payload: MatchWrite, *, actor: Member) -> Match:
        match = await self.get_match(match_id)
        self._ensure_can_modify(match, actor)
        await self._ensure_no_duplicate_members(payload)
        members_by_id = await self._ensure_members_exist(payload.team1 + payload.team2)
        await self._remember_placeholder_raw_names(payload)
        await self._ensure_player_name_classifications(payload.team1, payload.team2, members_by_id)

        match.match_date = date.fromisoformat(payload.date)
        match.match_type = payload.match_type
        match.note = payload.note
        match.updated_by = actor.pk

        if match.result_row is None:
            match.result_row = MatchResult(
                result=payload.result,
                map_name=payload.map_name,
                game_started_at=payload.game_started_at,
                duration_seconds=payload.duration_seconds,
            )
        else:
            match.result_row.result = payload.result
            match.result_row.map_name = payload.map_name
            match.result_row.game_started_at = payload.game_started_at
            match.result_row.duration_seconds = payload.duration_seconds

        match.participants.clear()
        await self._session.flush()
        match.participants.extend(
            self._build_participants(payload.team1, payload.team2, members_by_id, actor_pk=actor.pk)
        )

        if payload.attachment is None:
            if match.attachment is not None:
                await self._storage.delete(match.attachment.file_path)
                match.attachment = None
        else:
            await self._apply_attachment(match, payload.attachment, actor_pk=actor.pk)

        await self._session.commit()
        return await self._repo.refresh(match)

    async def delete_match(self, match_id: int, *, actor: Member) -> None:
        match = await self.get_match(match_id)
        self._ensure_can_delete(actor)
        if match.attachment is not None:
            await self._storage.delete(match.attachment.file_path)
        await self._repo.delete(match)
        await self._session.commit()

    async def delete_all_matches(self, *, actor: Member) -> int:
        """모든 경기기록을 삭제한다(운영자 제어판). 첨부(.rep) 파일도 스토리지에서 지운다.
        경기 행 삭제는 FK CASCADE로 참가자/첨부/결과까지 한 번에 정리된다. 반환값은 삭제된
        경기 수."""
        self._ensure_can_delete(actor)
        for _md, _mn, _fn, file_path in await self._repo.list_all_attachments():
            try:
                await self._storage.delete(file_path)
            except Exception:
                pass
        count = await self._repo.delete_all_matches()
        await self._session.commit()
        return count

    async def update_memo(self, match_id: int, note: str, *, actor: Member) -> Match:
        """정식 수정(update_match)과 달리 작성자/운영자 제한 없이 회원 누구나 남길 수 있는
        가벼운 메모 — note 한 필드만 바꾼다."""
        match = await self.get_match(match_id)
        match.note = note
        match.updated_by = actor.pk
        await self._session.commit()
        return await self._repo.refresh(match)

    def _ensure_can_modify(self, match: Match, actor: Member) -> None:
        if not actor.has_any_role("0202") and match.created_by != actor.pk:
            raise ForbiddenError("작성자 또는 운영자만 수정할 수 있습니다.")

    def _ensure_can_delete(self, actor: Member) -> None:
        # 삭제는 수정보다 엄격하게 — 작성자 본인이어도 안 되고 운영자만 가능하다(오삭제 방지).
        if not actor.has_any_role("0202"):
            raise ForbiddenError("운영자만 삭제할 수 있습니다.")

    def _player_name(self, slot: MatchSlot, members_by_id: dict[str, Member]) -> str:
        # 리플레이에서 파싱된 원본 게임 아이디, 또는 수기등록에서 프론트가 직접 고른
        # 이름은 무슨 일이 있어도 그대로 보존한다 — 회원으로 매칭됐든, 비회원/컴퓨터로
        # 남았든 상관없다(models.py의 MatchParticipant.player_name 참고). 예전엔 비회원/
        # 컴퓨터면 이 값을 버리고 공용 예약값으로 덮어썼는데, 그러면 그 사람이 실제로
        # 누구였는지가 영영 사라져서 나중에 유저 매핑 관리 화면에서 회원과 연결할 수조차
        # 없었다(실제로 지적받은 문제).
        if slot.player_name:
            return slot.player_name
        if is_computer_slot(slot.member_id):
            # 수기등록 컴퓨터 슬롯은 실제 게임 아이디가 없다 — 공용 예약값을 대신 넣는다
            # (schemas.MANUAL_COMPUTER_RAW_NAME, 마이그레이션에서 이미 replay_aliases에
            # kind="computer"로 심어둔 값).
            return MANUAL_COMPUTER_RAW_NAME
        if is_unregistered_slot(slot.member_id):
            return MANUAL_UNREGISTERED_RAW_NAME
        # 실제 회원 슬롯인데 이름을 안 보냈다(수기등록 화면이 아직 이름 선택 UI로
        # 바뀌지 않은 경우) — player_name은 절대 비워둘 수 없으므로, 그 회원이 등록해둔
        # 게임 아이디 중 가장 최근 것으로 대신한다(레거시 데이터 백필과 같은 전략,
        # migrations의 player_name 백필 마이그레이션 참고). 등록된 별칭이 하나도 없으면
        # (있을 수 없지만 방어적으로) 현재 배틀태그로 대신한다.
        member = members_by_id[slot.member_id]
        if member.replay_aliases:
            return member.replay_aliases[-1].raw_name
        return member.battletag

    def _build_participants(
        self,
        team1: list[MatchSlot],
        team2: list[MatchSlot],
        members_by_id: dict[str, Member],
        *,
        actor_pk: int,
    ) -> list[MatchParticipant]:
        participants = [
            MatchParticipant(
                team="team1",
                position=i,
                race=slot.race,
                player_name=self._player_name(slot, members_by_id),
                apm=slot.apm,
                eapm=slot.eapm,
                cmd_count=slot.cmd_count,
                effective_cmd_count=slot.effective_cmd_count,
                created_by=actor_pk,
                updated_by=actor_pk,
            )
            for i, slot in enumerate(team1)
        ]
        participants += [
            MatchParticipant(
                team="team2",
                position=i,
                race=slot.race,
                player_name=self._player_name(slot, members_by_id),
                apm=slot.apm,
                eapm=slot.eapm,
                cmd_count=slot.cmd_count,
                effective_cmd_count=slot.effective_cmd_count,
                created_by=actor_pk,
                updated_by=actor_pk,
            )
            for i, slot in enumerate(team2)
        ]
        return participants

    async def _ensure_player_name_classifications(
        self,
        team1: list[MatchSlot],
        team2: list[MatchSlot],
        members_by_id: dict[str, Member],
    ) -> None:
        """실제 회원 슬롯에 그 회원의 replay_aliases에 아직 없는 새 player_name이 쓰이면,
        그 이름을 즉시 이 회원의 별칭으로 등록해 이후 조회(_to_match_slot, 통계 집계)가
        곧바로 이 회원으로 연결되게 한다 — "수기입력 시 선택한 이름을 회원과 연결한다"가
        구현되는 지점이다. 이미 이 회원의 별칭이면 손대지 않는다. 다른 회원이나 컴퓨터/
        비회원으로 이미 등록된 이름을 쓰려고 하면(예: 오타로 남의 아이디를 고른 경우)
        충돌로 보고 거부한다 — replay_aliases.raw_name은 항상 하나의 대상만 가리켜야
        목록/통계가 꼬이지 않는다."""
        for slot in team1 + team2:
            if is_placeholder_slot(slot.member_id) or not slot.player_name:
                continue
            member = members_by_id[slot.member_id]
            if slot.player_name in {a.raw_name for a in member.replay_aliases}:
                continue
            existing = await self._repo.get_alias_by_raw_name(slot.player_name)
            if existing is not None:
                raise ValidationError(f"'{slot.player_name}'은(는) 이미 다른 대상으로 등록된 이름입니다.")
            member.replay_aliases.append(ReplayAlias(raw_name=slot.player_name, kind="member"))

    async def _remember_placeholder_raw_names(self, payload: MatchWrite) -> None:
        """리플레이에서 컴퓨터(AI)/비회원으로 등록되는 슬롯의 분류를 replay_aliases에 남긴다.

        새 게임아이디(rawName)는 저장 전에 반드시 회원/컴퓨터/비회원 중 하나로 확정되고,
        미분류인 채로 저장되는 경로가 없다(요청: "매핑 안 하고 저장할 경로가 없으니 그
        분류를 alias 테이블에 자동 등록하는 게 맞다"). 그래서 회원은 _associate_member_aliases가,
        컴퓨터/비회원은 여기서 각각 kind='computer'/'unregistered'로 자동 등록해
        replay_aliases를 모든 게임아이디의 단일 레지스트리로 유지한다 — 게임아이디 화면에
        컴퓨터/비회원도 바로 뜨고, 다음 리플레이에서 같은 이름을 또 물어보지 않는다.
        (예전엔 비회원을 일부러 안 남겼는데, 그 이름을 나중에 회원으로 연결할 기회를
        지키려는 의도였다 — 이제 그 연결은 게임아이디 화면 재매핑으로 하면 되고,
        set_replay_name_mapping이 기존 별칭을 지우고 회원으로 다시 건다.)

        이미 있는 매핑은 절대 건드리지 않는다 — 특히 kind='member'(누군가의 게임 아이디로
        이미 등록된 이름)를 덮어쓰면 그 회원의 과거 경기 매칭이 통째로 어긋난다."""
        for slot in payload.team1 + payload.team2:
            if not slot.player_name:
                continue
            # 수기등록 슬롯이 쓰는 공용 예약값은 마이그레이션이 이미 심어둔 시스템 행이다.
            if slot.player_name in (MANUAL_COMPUTER_RAW_NAME, MANUAL_UNREGISTERED_RAW_NAME):
                continue
            if is_computer_slot(slot.member_id):
                kind = "computer"
            elif is_unregistered_slot(slot.member_id):
                kind = "unregistered"
            else:
                continue
            if await self._repo.replay_alias_exists(slot.player_name):
                continue
            self._repo.add_replay_name_classification(ReplayAlias(raw_name=slot.player_name, kind=kind))

    async def _ensure_no_duplicate_members(self, payload: MatchWrite) -> None:
        # 컴퓨터/비회원 슬롯은 실제 회원이 아니라 여러 개 있어도 "중복"이 아니므로 제외한다.
        ids = [
            s.member_id
            for s in payload.team1 + payload.team2
            if not is_placeholder_slot(s.member_id)
        ]
        if len(ids) != len(set(ids)):
            raise ValidationError("같은 회원이 양 팀에 동시에 포함될 수 없습니다.")

    async def _ensure_members_exist(self, slots: list[MatchSlot]) -> dict[str, Member]:
        members_by_id: dict[str, Member] = {}
        for member_id in {s.member_id for s in slots if not is_placeholder_slot(s.member_id)}:
            member = await self._member_repo.get_by_login_id(member_id)
            if member is None:
                raise NotFoundError(f"존재하지 않는 회원입니다: {member_id}")
            members_by_id[member_id] = member
        return members_by_id

    async def _apply_attachment(
        self, match: Match, payload: MatchAttachmentPayload, *, actor_pk: int
    ) -> None:
        if not is_data_url(payload.url):
            return  # 기존에 저장된 첨부파일 URL 그대로 유지 (변경 없음)

        if not payload.name.lower().endswith(".rep"):
            raise ValidationError("스타크래프트 리플레이 파일(.rep)만 첨부할 수 있습니다.")

        content, content_type = decode_data_url(payload.url)
        ext = guess_extension(content_type, payload.name)
        stored = await self._storage.save(
            subdir="matches",
            filename=payload.name or f"attachment{ext}",
            content=content,
            content_type=content_type,
        )
        if match.attachment is not None:
            await self._storage.delete(match.attachment.file_path)
            match.attachment.file_name = payload.name
            match.attachment.file_path = stored.path
            match.attachment.content_type = content_type
            match.attachment.file_size = len(content)
            match.attachment.updated_by = actor_pk
        else:
            match.attachment = MatchAttachment(
                file_name=payload.name,
                file_path=stored.path,
                content_type=content_type,
                file_size=len(content),
                created_by=actor_pk,
                updated_by=actor_pk,
            )
