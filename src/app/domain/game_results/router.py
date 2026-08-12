from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Query, status
from fastapi.responses import Response

from app.api.deps import CurrentAdmin, CurrentMember, DbSession, StorageDep
from app.core.exceptions import NotFoundError
from app.domain.game_results.schemas import (
    MinimapWalkWrite,
    DuplicateCheckRequest,
    DuplicateCheckResponse,
    EarliestDateResponse,
    GameResultOut,
    GameResultPage,
    GameResultReplayMerge,
    GameResultReplayMergeResult,
    GameResultStatsResponse,
    GameResultWrite,
    MapCatalog,
    MinimapAssignWrite,
    MinimapImageOut,
    MinimapImageWrite,
    RivalryResponse,
    RatingHistoryResponse,
    ReplayMapList,
    SummaryRewrite,
    ReplayNameClassificationEntry,
    ReplayNameClassificationLookupRequest,
    ReplayNameClassificationLookupResponse,
    ReplayNameClassificationWrite,
    ReplayNameMappingEntry,
    ReplayNameMappingListResponse,
    ReplayNameMappingMember,
    ReplayNameMappingWrite,
)
from app.domain.game_results.service import GameResultService, to_game_result_out


# prefix는 상위(api/router.py)에서 /game-results로 붙인다. 한때 옛 경로 /api/matches로도
# 같은 라우터를 한 벌 더 끼워 뒀지만(배포가 어긋나는 순간 대비), 프론트가 새 경로만 쓰게 된
# 뒤라 지웠다.
router = APIRouter(tags=["game-results"])


@router.get("", response_model=GameResultPage)
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
) -> GameResultPage:
    service = GameResultService(db, storage)
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
    return GameResultPage(
        items=[
            to_game_result_out(m, storage, alias_by_player_name, actor_pk=current.pk, is_admin=is_admin)
            for m in matches
        ],
        next_cursor=next_cursor,
        has_more=has_more,
        total=total,
    )


@router.get("/stats", response_model=GameResultStatsResponse)
async def get_stats(
    db: DbSession,
    storage: StorageDep,
    _current: CurrentMember,
    member_ids: str | None = Query(default=None, alias="memberIds"),
    date_from: str | None = Query(default=None, alias="dateFrom"),
    date_to: str | None = Query(default=None, alias="dateTo"),
    match_type: str | None = Query(default=None, alias="matchType"),
    race: str | None = None,
) -> GameResultStatsResponse:
    ids = [i.strip() for i in member_ids.split(",") if i.strip()] if member_ids else None
    members = await GameResultService(db, storage).get_stats(
        member_ids=ids,
        date_from=date_from,
        date_to=date_to,
        match_type=match_type,
        race=race,
    )
    return GameResultStatsResponse(members=members)


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
    return await GameResultService(db, storage).get_rating_history(
        member_id=member_id, match_type=match_type, date_from=date_from, date_to=date_to, race=race,
    )


@router.get("/stats/rivalries", response_model=RivalryResponse)
async def get_rivalries(
    db: DbSession,
    storage: StorageDep,
    _current: CurrentMember,
    date_from: str | None = Query(default=None, alias="dateFrom"),
    date_to: str | None = Query(default=None, alias="dateTo"),
    # solo(기본) = 1:1 경기만, team = 팀전을 개인 단위 쌍으로 환산(상성맵 팀전 탭).
    mode: Literal["solo", "team"] = Query(default="solo"),
) -> RivalryResponse:
    # 유저 상성(상대전적 쌍) — 상성 맵 화면이 쓴다.
    return await GameResultService(db, storage).get_rivalries(
        date_from=date_from, date_to=date_to, team=(mode == "team"),
    )


@router.get("/earliest-date", response_model=EarliestDateResponse)
async def get_earliest_date(
    db: DbSession, storage: StorageDep, _current: CurrentMember
) -> EarliestDateResponse:
    earliest = await GameResultService(db, storage).get_earliest_match_date()
    return EarliestDateResponse(date=earliest)


@router.get("/replay-maps", response_model=ReplayMapList)
async def list_replay_maps(
    db: DbSession, storage: StorageDep, _current: CurrentMember,
    hash: list[str] = Query(default_factory=list),
) -> ReplayMapList:
    """미니맵 격자를 해시로 받아 온다(?hash=..&hash=..).

    경기 응답에는 해시만 실려 있다 — 격자 하나가 22KB인데 같은 맵을 쓰는 경기가 수십 건이라,
    목록에 끼워 보내면 같은 값이 계속 되풀이된다. 그래서 클라이언트가 아직 안 받아 둔 해시만
    모아 여기로 묻고 한 번 받은 것은 계속 들고 쓴다(내용 해시라 절대 바뀌지 않는다).
    """
    maps = await GameResultService(db, storage).list_replay_maps(hash)
    return ReplayMapList(maps=maps)


@router.get("/replay-maps/catalog", response_model=MapCatalog)
async def map_catalog(db: DbSession, storage: StorageDep, _admin: CurrentAdmin) -> MapCatalog:
    """제어판 — 등록된 맵과 올려 둔 미니맵 그림 목록(격자는 빼고)."""
    return await GameResultService(db, storage).map_catalog()


@router.post("/replay-maps/images", response_model=MinimapImageOut)
async def create_minimap_image(
    payload: MinimapImageWrite, db: DbSession, storage: StorageDep, _admin: CurrentAdmin
) -> MinimapImageOut:
    """실제 미니맵 그림을 한 장 올린다. hashes를 함께 주면 그 맵들이 이 그림을 쓴다."""
    return await GameResultService(db, storage).create_minimap_image(payload)


@router.put("/replay-maps/images/{image_id}", response_model=MinimapImageOut)
async def update_minimap_image(
    image_id: int, payload: MinimapImageWrite, db: DbSession, storage: StorageDep, _admin: CurrentAdmin
) -> MinimapImageOut:
    """등록된 미니맵의 이름·그림을 고친다(요청: 미니맵 메뉴에서 그림 변경) — 지웠다 다시
    올리면 붙어 있던 맵 매핑이 통째로 풀린다. image를 빼면 이름만 바뀐다."""
    return await GameResultService(db, storage).update_minimap_image(image_id, payload)


@router.put("/replay-maps/images/{image_id}/walk", response_model=MinimapImageOut)
async def update_minimap_walk(
    image_id: int, payload: MinimapWalkWrite, db: DbSession, storage: StorageDep,
    _current: CurrentMember,
) -> MinimapImageOut:
    """지형(이동 가능/불가) 격자만 고친다 — 회원 누구나(요청: 아무나 지형 업데이트).

    그림·이름·매핑은 운영자 몫 그대로고, 지형은 보는 사람이 제일 많이 아는 값이라
    문을 넓힌다. 빈 문자열이면 지운다(자동 어림으로 복귀)."""
    return await GameResultService(db, storage).update_minimap_walk(image_id, payload.walk)


@router.delete("/replay-maps/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_minimap_image(
    image_id: int, db: DbSession, storage: StorageDep, _admin: CurrentAdmin
) -> None:
    await GameResultService(db, storage).delete_minimap_image(image_id)


@router.post("/replay-maps/assign")
async def assign_minimap_image(
    payload: MinimapAssignWrite, db: DbSession, storage: StorageDep, _admin: CurrentAdmin
) -> dict[str, int]:
    """맵 여러 개를 한 그림에 묶거나 떼어 낸다(요청: 이름·판본만 다른 맵을 한데 묶기)."""
    changed = await GameResultService(db, storage).assign_minimap_image(payload)
    return {"changed": changed}


@router.post("/duplicate-check", response_model=DuplicateCheckResponse)
async def check_duplicates(
    payload: DuplicateCheckRequest, db: DbSession, storage: StorageDep, _current: CurrentMember
) -> DuplicateCheckResponse:
    existing = await GameResultService(db, storage).check_duplicates(payload.game_started_at)
    return DuplicateCheckResponse(existing=existing)


@router.post("/merge-replay", response_model=GameResultReplayMergeResult)
async def merge_replay(
    payload: GameResultReplayMerge, db: DbSession, storage: StorageDep, current: CurrentMember
) -> GameResultReplayMergeResult:
    match = await GameResultService(db, storage).merge_replay(payload, actor=current)
    return GameResultReplayMergeResult(merged=match is not None, match_no=match.match_no if match else None)


@router.post("/replay-name-classifications/lookup", response_model=ReplayNameClassificationLookupResponse)
async def lookup_replay_name_classifications(
    payload: ReplayNameClassificationLookupRequest, db: DbSession, storage: StorageDep, _current: CurrentMember
) -> ReplayNameClassificationLookupResponse:
    rows = await GameResultService(db, storage).lookup_replay_name_classifications(payload.raw_names)
    return ReplayNameClassificationLookupResponse(
        classifications=[ReplayNameClassificationEntry(raw_name=r.raw_name, kind=r.kind) for r in rows]
    )


@router.post("/replay-name-classifications", response_model=ReplayNameClassificationEntry)
async def set_replay_name_classification(
    payload: ReplayNameClassificationWrite, db: DbSession, storage: StorageDep, _current: CurrentMember
) -> ReplayNameClassificationEntry:
    entry = await GameResultService(db, storage).set_replay_name_classification(payload.raw_name, payload.kind)
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
    rows = await GameResultService(db, storage).list_replay_name_mappings()
    return ReplayNameMappingListResponse(entries=[_to_mapping_entry(r) for r in rows])


@router.post("/replay-name-mappings", response_model=ReplayNameMappingEntry)
async def set_replay_name_mapping(
    payload: ReplayNameMappingWrite, db: DbSession, storage: StorageDep, admin: CurrentAdmin
) -> ReplayNameMappingEntry:
    row = await GameResultService(db, storage).set_replay_name_mapping(
        payload.raw_name, payload.kind, payload.member_id, actor_pk=admin.pk,
    )
    return _to_mapping_entry(row)


@router.get("/replays/archive")
async def download_replay_archive(db: DbSession, storage: StorageDep, _admin: CurrentAdmin) -> Response:
    """등록된 모든 리플레이(.rep)를 zip으로 묶어 다운로드(운영자 전용)."""
    data = await GameResultService(db, storage).build_replay_archive()
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="replays.zip"'},
    )


# "/all"은 "/{match_id}"(int)보다 먼저 선언해야 한다 — 뒤에 두면 match_id 파싱 실패로 422.
@router.delete("/all")
async def delete_all_matches(db: DbSession, storage: StorageDep, admin: CurrentAdmin) -> dict[str, int]:
    """모든 경기기록 삭제(운영자 제어판). 첨부(.rep) 파일도 함께 지운다."""
    count = await GameResultService(db, storage).delete_all_matches(actor=admin)
    return {"deleted": count}


@router.post("", response_model=GameResultOut)
async def create_match(
    payload: GameResultWrite, db: DbSession, storage: StorageDep, current: CurrentMember
) -> GameResultOut:
    service = GameResultService(db, storage)
    match = await service.create_match(payload, actor=current)
    return to_game_result_out(
        match, storage, await service.alias_by_player_name(),
        actor_pk=current.pk, is_admin=current.has_any_role("0202"),
    )


@router.post("/{match_id}/summary", status_code=status.HTTP_204_NO_CONTENT)
async def rewrite_summary(
    match_id: int,
    payload: SummaryRewrite,
    db: DbSession,
    storage: StorageDep,
    _: CurrentAdmin,
) -> None:
    """등록된 경기의 요약만 다시 써 넣는다(요청: 요약 재분석) — 경기 내용은 안 건드린다.
    요약을 만드는 파서가 브라우저 쪽에만 있어서, 화면이 리플레이를 다시 분석해 보내온다."""
    await GameResultService(db, storage).rewrite_summary(match_id, payload)


@router.get("/{match_id}", response_model=GameResultOut)
async def get_match(
    match_id: int, db: DbSession, storage: StorageDep, current: CurrentMember
) -> GameResultOut:
    # 카카오톡 공유 링크가 여는 "이 경기만 보이는" 화면에서 단건 조회에 쓴다. 정적 GET
    # 경로(/stats, /ranking 등)보다 아래에 선언해 int 경로변수가 그것들을 가리지 않게 한다.
    service = GameResultService(db, storage)
    match = await service.get_match(match_id)
    return to_game_result_out(
        match, storage, await service.alias_by_player_name(),
        actor_pk=current.pk, is_admin=current.has_any_role("0202"),
    )


@router.delete("/{match_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_match(
    match_id: int, db: DbSession, storage: StorageDep, current: CurrentMember
) -> None:
    await GameResultService(db, storage).delete_match(match_id, actor=current)


@router.get("/{match_id}/replay")
async def download_replay(
    match_id: int, db: DbSession, storage: StorageDep, _current: CurrentMember
) -> Response:
    match = await GameResultService(db, storage).get_match(match_id)
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

