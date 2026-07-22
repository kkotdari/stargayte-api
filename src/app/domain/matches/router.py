from datetime import date
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Query, status
from fastapi.responses import Response

from app.api.deps import CurrentAdmin, CurrentMember, DbSession, StorageDep
from app.core.exceptions import NotFoundError
from app.domain.matches.schemas import (
    DuplicateCheckRequest,
    DuplicateCheckResponse,
    EarliestDateResponse,
    MainRaceResponse,
    MatchNoteOut,
    MatchNoteWrite,
    MatchOut,
    MatchPage,
    MatchReplayMerge,
    MatchReplayMergeResult,
    MatchStatsResponse,
    MatchWrite,
    MonthlyMatchStatsResponse,
    RivalryResponse,
    MonthlyTeamRankingResponse,
    RankingResponse,
    RatingHistoryResponse,
    ReplayNameClassificationEntry,
    ReplayNameClassificationLookupRequest,
    ReplayNameClassificationLookupResponse,
    ReplayNameClassificationWrite,
    ReplayNameMappingEntry,
    ReplayNameMappingListResponse,
    ReplayNameMappingMember,
    ReplayNameMappingWrite,
    TeamRankingResponse,
)
from app.domain.matches.service import MatchService, to_match_out


def _split_months(months: str) -> list[str]:
    return [m.strip() for m in months.split(",") if m.strip()]

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("", response_model=MatchPage)
async def list_matches(
    db: DbSession,
    storage: StorageDep,
    current: CurrentMember,
    cursor: str | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    sort: Literal["latest", "oldest"] = "latest",
    date_from: str | None = Query(default=None, alias="dateFrom"),
    date_to: str | None = Query(default=None, alias="dateTo"),
    match_type: str | None = Query(default=None, alias="matchType"),
    user_query: str | None = Query(default=None, alias="userQuery"),
    match_all_users: bool = Query(default=False, alias="matchAllUsers"),
    has_placeholder: bool = Query(default=False, alias="hasPlaceholder"),
    # 팀 랭킹에서 팀 하나를 눌렀을 때 — 이 회원들이 전부 "같은 편"으로 뛴 경기만 추린다
    # (전원이 참가한 경기로만 찾으면 서로 상대편이었던 경기까지 딸려온다).
    team_member_ids: str | None = Query(default=None, alias="teamMemberIds"),
) -> MatchPage:
    service = MatchService(db, storage)
    team_ids = [i.strip() for i in team_member_ids.split(",") if i.strip()] if team_member_ids else None
    matches, next_cursor, has_more = await service.list_matches_page(
        cursor=cursor,
        limit=limit,
        sort=sort,
        date_from=date_from,
        date_to=date_to,
        match_type=match_type,
        user_query=user_query,
        match_all_users=match_all_users,
        has_placeholder=has_placeholder,
        team_member_ids=team_ids,
    )
    # 첫 페이지(커서 없음)에서만 전체 건수를 센다 — 스크롤로 다음 페이지를 불러올 때마다
    # 다시 셀 필요는 없다(프론트가 첫 응답 값을 그대로 들고 있는다).
    total = (
        await service.count_matches(
            date_from=date_from,
            date_to=date_to,
            match_type=match_type,
            user_query=user_query,
            match_all_users=match_all_users,
            has_placeholder=has_placeholder,
            team_member_ids=team_ids,
        )
        if cursor is None
        else None
    )
    # 목록 안의 매치 여러 개를 직렬화하는 동안 재사용 — 매치마다 다시 조회하지 않는다.
    alias_by_player_name = await service.alias_by_player_name()
    is_admin = current.has_any_role("0202")
    return MatchPage(
        items=[
            to_match_out(m, storage, alias_by_player_name, actor_pk=current.pk, is_admin=is_admin)
            for m in matches
        ],
        next_cursor=next_cursor,
        has_more=has_more,
        total=total,
    )


@router.get("/stats", response_model=MatchStatsResponse)
async def get_stats(
    db: DbSession,
    storage: StorageDep,
    _current: CurrentMember,
    member_ids: str | None = Query(default=None, alias="memberIds"),
    date_from: str | None = Query(default=None, alias="dateFrom"),
    date_to: str | None = Query(default=None, alias="dateTo"),
    match_type: str | None = Query(default=None, alias="matchType"),
    race: str | None = None,
) -> MatchStatsResponse:
    ids = [i.strip() for i in member_ids.split(",") if i.strip()] if member_ids else None
    members = await MatchService(db, storage).get_stats(
        member_ids=ids,
        date_from=date_from,
        date_to=date_to,
        match_type=match_type,
        race=race,
    )
    return MatchStatsResponse(members=members)


@router.get("/ranking", response_model=RankingResponse)
async def get_ranking(
    db: DbSession,
    storage: StorageDep,
    _current: CurrentMember,
    date_from: str | None = Query(default=None, alias="dateFrom"),
    date_to: str | None = Query(default=None, alias="dateTo"),
    match_type: str | None = Query(default=None, alias="matchType"),
    race: str | None = None,
) -> RankingResponse:
    # 랭킹 조회 — 순위/레이팅(+전적)을 회원별로 내려준다. 산정 로직은 전적통계(/stats)와
    # 공유하지만 URL은 의미(랭킹)에 맞춘다(요청). member_ids는 랭킹에선 안 쓰므로 뺐다.
    members = await MatchService(db, storage).get_stats(
        member_ids=None,
        date_from=date_from,
        date_to=date_to,
        match_type=match_type,
        race=race,
    )
    return RankingResponse(members=members)


@router.get("/rating-history", response_model=RatingHistoryResponse)
async def get_rating_history(
    db: DbSession,
    storage: StorageDep,
    _current: CurrentMember,
    member_id: str = Query(alias="memberId"),
    match_type: str | None = Query(default=None, alias="matchType"),
    date_from: str | None = Query(default=None, alias="dateFrom"),
    date_to: str | None = Query(default=None, alias="dateTo"),
    race: str | None = None,
) -> RatingHistoryResponse:
    # 랭킹 상세의 '경기당 레이팅 변화(Δ)' — 이 회원이 뛴 경기마다의 μ 증감(match_no로 키잉).
    # 랭킹이 조회 기간(dateFrom~dateTo)만으로 리셋해 매겨지므로, 여기도 같은 기간만 재생해야
    # 목록의 μ/σ와 이 상세의 Δ 합이 서로 어긋나지 않는다. 종족 필터 시 그 종족 Δ만 나온다.
    return await MatchService(db, storage).get_rating_history(
        member_id=member_id, match_type=match_type, date_from=date_from, date_to=date_to, race=race,
    )


@router.get("/team-ranking", response_model=TeamRankingResponse)
async def get_team_ranking(
    db: DbSession,
    storage: StorageDep,
    _current: CurrentMember,
    # 랭킹 화면의 월 기준 기본 집계용 — 안 넘기면 예전처럼 전체 기간이 대상이다.
    date_from: str | None = Query(default=None, alias="dateFrom"),
    date_to: str | None = Query(default=None, alias="dateTo"),
) -> TeamRankingResponse:
    return await MatchService(db, storage).get_team_ranking(
        date_from=date.fromisoformat(date_from) if date_from else None,
        date_to=date.fromisoformat(date_to) if date_to else None,
    )


@router.get("/stats/rivalries", response_model=RivalryResponse)
async def get_rivalries(
    db: DbSession,
    storage: StorageDep,
    _current: CurrentMember,
    date_from: str | None = Query(default=None, alias="dateFrom"),
    date_to: str | None = Query(default=None, alias="dateTo"),
) -> RivalryResponse:
    # 유저 상성(1:1 상대전적 쌍) — 통계 화면 하단의 상성 맵이 쓴다.
    return await MatchService(db, storage).get_rivalries(date_from=date_from, date_to=date_to)


@router.get("/stats/monthly", response_model=MonthlyMatchStatsResponse)
async def get_stats_monthly(
    db: DbSession,
    storage: StorageDep,
    _current: CurrentMember,
    # "YYYY-MM" 쉼표 목록 — 목록의 전월 대비 화살표(2개월)나 카드 클릭 시 최근 5개월
    # 순위변동 모달이 한 번에 여러 달을 요청한다(요청: "api로 랭킹 목록 가져올때
    # 배열형태로 파라미터 추가").
    months: str = Query(alias="months"),
    member_ids: str | None = Query(default=None, alias="memberIds"),
    match_type: str | None = Query(default=None, alias="matchType"),
    race: str | None = None,
) -> MonthlyMatchStatsResponse:
    ids = [i.strip() for i in member_ids.split(",") if i.strip()] if member_ids else None
    result = await MatchService(db, storage).get_stats_monthly(
        months=_split_months(months), member_ids=ids, match_type=match_type, race=race,
    )
    return MonthlyMatchStatsResponse(months=result)


@router.get("/team-ranking/monthly", response_model=MonthlyTeamRankingResponse)
async def get_team_ranking_monthly(
    db: DbSession,
    storage: StorageDep,
    _current: CurrentMember,
    months: str = Query(alias="months"),
) -> MonthlyTeamRankingResponse:
    result = await MatchService(db, storage).get_team_ranking_monthly(months=_split_months(months))
    return MonthlyTeamRankingResponse(months=result)


@router.get("/main-race", response_model=MainRaceResponse)
async def get_main_race(
    db: DbSession,
    storage: StorageDep,
    _current: CurrentMember,
    member_id: str = Query(alias="memberId"),
    date_from: str | None = Query(default=None, alias="dateFrom"),
    date_to: str | None = Query(default=None, alias="dateTo"),
    match_type: str | None = Query(default=None, alias="matchType"),
) -> MainRaceResponse:
    race = await MatchService(db, storage).get_main_race(
        member_id=member_id,
        date_from=date_from,
        date_to=date_to,
        match_type=match_type,
    )
    return MainRaceResponse(race=race)


@router.get("/earliest-date", response_model=EarliestDateResponse)
async def get_earliest_date(
    db: DbSession, storage: StorageDep, _current: CurrentMember
) -> EarliestDateResponse:
    earliest = await MatchService(db, storage).get_earliest_match_date()
    return EarliestDateResponse(date=earliest)


@router.post("/duplicate-check", response_model=DuplicateCheckResponse)
async def check_duplicates(
    payload: DuplicateCheckRequest, db: DbSession, storage: StorageDep, _current: CurrentMember
) -> DuplicateCheckResponse:
    existing = await MatchService(db, storage).check_duplicates(payload.game_started_at)
    return DuplicateCheckResponse(existing=existing)


@router.post("/merge-replay", response_model=MatchReplayMergeResult)
async def merge_replay(
    payload: MatchReplayMerge, db: DbSession, storage: StorageDep, current: CurrentMember
) -> MatchReplayMergeResult:
    match = await MatchService(db, storage).merge_replay(payload, actor=current)
    return MatchReplayMergeResult(merged=match is not None, match_no=match.match_no if match else None)


@router.post("/replay-name-classifications/lookup", response_model=ReplayNameClassificationLookupResponse)
async def lookup_replay_name_classifications(
    payload: ReplayNameClassificationLookupRequest, db: DbSession, storage: StorageDep, _current: CurrentMember
) -> ReplayNameClassificationLookupResponse:
    rows = await MatchService(db, storage).lookup_replay_name_classifications(payload.raw_names)
    return ReplayNameClassificationLookupResponse(
        classifications=[ReplayNameClassificationEntry(raw_name=r.raw_name, kind=r.kind) for r in rows]
    )


@router.post("/replay-name-classifications", response_model=ReplayNameClassificationEntry)
async def set_replay_name_classification(
    payload: ReplayNameClassificationWrite, db: DbSession, storage: StorageDep, _current: CurrentMember
) -> ReplayNameClassificationEntry:
    entry = await MatchService(db, storage).set_replay_name_classification(payload.raw_name, payload.kind)
    return ReplayNameClassificationEntry(raw_name=entry.raw_name, kind=entry.kind)


def _to_mapping_entry(row: dict) -> ReplayNameMappingEntry:
    member = row["member"]
    return ReplayNameMappingEntry(
        raw_name=row["raw_name"],
        kind=row["kind"],
        member=ReplayNameMappingMember(
            id=member.id, nickname=member.nickname, battletag=member.battletag, avatar=member.avatar_url,
        ) if member is not None else None,
        last_seen=row.get("last_seen"),
        has_matches=row.get("has_matches", False),
    )


@router.get("/replay-name-mappings", response_model=ReplayNameMappingListResponse)
async def list_replay_name_mappings(db: DbSession, storage: StorageDep, _current: CurrentMember) -> ReplayNameMappingListResponse:
    # 조회는 회원 누구나 가능 — 실제 수정/삭제(아래 두 엔드포인트)만 운영자로 제한한다.
    rows = await MatchService(db, storage).list_replay_name_mappings()
    return ReplayNameMappingListResponse(entries=[_to_mapping_entry(r) for r in rows])


@router.post("/replay-name-mappings", response_model=ReplayNameMappingEntry)
async def set_replay_name_mapping(
    payload: ReplayNameMappingWrite, db: DbSession, storage: StorageDep, admin: CurrentAdmin
) -> ReplayNameMappingEntry:
    row = await MatchService(db, storage).set_replay_name_mapping(
        payload.raw_name, payload.kind, payload.member_id, actor_pk=admin.pk,
    )
    return _to_mapping_entry(row)


@router.delete("/replay-name-mappings/{raw_name}", status_code=204)
async def delete_replay_name_mapping(
    raw_name: str, db: DbSession, storage: StorageDep, _admin: CurrentAdmin
) -> None:
    await MatchService(db, storage).delete_replay_name_mapping(raw_name)


@router.get("/replays/archive")
async def download_replay_archive(db: DbSession, storage: StorageDep, _admin: CurrentAdmin) -> Response:
    """등록된 모든 리플레이(.rep)를 zip으로 묶어 다운로드(운영자 전용)."""
    data = await MatchService(db, storage).build_replay_archive()
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="replays.zip"'},
    )


# "/all"은 "/{match_id}"(int)보다 먼저 선언해야 한다 — 뒤에 두면 match_id 파싱 실패로 422.
@router.delete("/all")
async def delete_all_matches(db: DbSession, storage: StorageDep, admin: CurrentAdmin) -> dict[str, int]:
    """모든 경기기록 삭제(운영자 제어판). 첨부(.rep) 파일도 함께 지운다."""
    count = await MatchService(db, storage).delete_all_matches(actor=admin)
    return {"deleted": count}


@router.post("", response_model=MatchOut)
async def create_match(
    payload: MatchWrite, db: DbSession, storage: StorageDep, current: CurrentMember
) -> MatchOut:
    service = MatchService(db, storage)
    match = await service.create_match(payload, actor=current)
    return to_match_out(
        match, storage, await service.alias_by_player_name(),
        actor_pk=current.pk, is_admin=current.has_any_role("0202"),
    )


@router.put("/{match_id}", response_model=MatchOut)
async def update_match(
    match_id: int,
    payload: MatchWrite,
    db: DbSession,
    storage: StorageDep,
    current: CurrentMember,
) -> MatchOut:
    service = MatchService(db, storage)
    match = await service.update_match(match_id, payload, actor=current)
    return to_match_out(
        match, storage, await service.alias_by_player_name(),
        actor_pk=current.pk, is_admin=current.has_any_role("0202"),
    )


# ── 경기 댓글(메모) — 게시판 댓글처럼 회원 누구나 한 줄(최대 50자), 본인/운영자만 수정·삭제 ──
@router.get("/{match_id}/notes", response_model=list[MatchNoteOut])
async def list_match_notes(
    match_id: int, db: DbSession, storage: StorageDep, current: CurrentMember
) -> list[MatchNoteOut]:
    return await MatchService(db, storage).list_notes(match_id, actor=current)


@router.post("/{match_id}/notes", response_model=MatchNoteOut)
async def create_match_note(
    match_id: int,
    payload: MatchNoteWrite,
    db: DbSession,
    storage: StorageDep,
    current: CurrentMember,
) -> MatchNoteOut:
    return await MatchService(db, storage).create_note(
        match_id, payload.text, payload.target_member_ids, actor=current
    )


@router.patch("/{match_id}/notes/{note_id}", response_model=MatchNoteOut)
async def update_match_note(
    match_id: int,
    note_id: int,
    payload: MatchNoteWrite,
    db: DbSession,
    storage: StorageDep,
    current: CurrentMember,
) -> MatchNoteOut:
    return await MatchService(db, storage).update_note(
        note_id, payload.text, payload.target_member_ids, actor=current
    )


@router.delete("/{match_id}/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_match_note(
    match_id: int,
    note_id: int,
    db: DbSession,
    storage: StorageDep,
    current: CurrentMember,
) -> None:
    await MatchService(db, storage).delete_note(note_id, actor=current)


@router.get("/{match_id}", response_model=MatchOut)
async def get_match(
    match_id: int, db: DbSession, storage: StorageDep, current: CurrentMember
) -> MatchOut:
    # 카카오톡 공유 링크가 여는 "이 경기만 보이는" 화면에서 단건 조회에 쓴다. 정적 GET
    # 경로(/stats, /ranking 등)보다 아래에 선언해 int 경로변수가 그것들을 가리지 않게 한다.
    service = MatchService(db, storage)
    match = await service.get_match(match_id)
    return to_match_out(
        match, storage, await service.alias_by_player_name(),
        actor_pk=current.pk, is_admin=current.has_any_role("0202"),
    )


@router.delete("/{match_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_match(
    match_id: int, db: DbSession, storage: StorageDep, current: CurrentMember
) -> None:
    await MatchService(db, storage).delete_match(match_id, actor=current)


@router.get("/{match_id}/replay")
async def download_replay(
    match_id: int, db: DbSession, storage: StorageDep, _current: CurrentMember
) -> Response:
    match = await MatchService(db, storage).get_match(match_id)
    replay = match.result_row.replay if match.result_row else None
    if replay is None:
        raise NotFoundError("리플레이가 없습니다.")

    content = await storage.read(replay.file_path)
    filename = replay.display_name
    # 파일명에 한글이 섞여 있어도 안전하도록 ASCII fallback + RFC 5987 filename* 둘 다 넣는다.
    ascii_fallback = filename.encode("ascii", "ignore").decode("ascii") or "replay.rep"
    disposition = f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=content,
        media_type=replay.content_type or "application/octet-stream",
        headers={"Content-Disposition": disposition},
    )

