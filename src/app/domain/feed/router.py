from fastapi import APIRouter, Query, status

from app.api.deps import CurrentAdmin, CurrentMember, DbSession
from app.domain.feed.schemas import (
    FeedCommentCreate,
    FeedCommentOut,
    FeedCommentWrite,
    FeedTargetTypeInput,
    RankingRecomputeResult,
    RankingShiftOut,
)
from app.domain.feed.service import FeedCommentService, RankingShiftService

router = APIRouter(prefix="/feed", tags=["feed"])


@router.get("/comments/all", response_model=list[FeedCommentOut])
async def list_all_feed_comments(db: DbSession, current: CurrentMember) -> list[FeedCommentOut]:
    """피드가 목록을 부를 때 댓글도 한 번에 같이 받아 간다(요청) — 카드마다 따로 부르면
    답이 제각각 도착하며 카드 키가 뒤늦게 자라 스크롤 자리가 밀린다."""
    return await FeedCommentService(db).list_all(actor=current)


@router.get("/comments", response_model=list[FeedCommentOut])
async def list_feed_comments(
    db: DbSession, current: CurrentMember,
    target_type: FeedTargetTypeInput = Query(alias="targetType"),
    target_id: int = Query(alias="targetId"),
) -> list[FeedCommentOut]:
    return await FeedCommentService(db).list_for_target(target_type, target_id, actor=current)


@router.post("/comments", response_model=FeedCommentOut, status_code=status.HTTP_201_CREATED)
async def create_feed_comment(
    payload: FeedCommentCreate, db: DbSession, current: CurrentMember
) -> FeedCommentOut:
    return await FeedCommentService(db).create(
        payload.target_type, payload.target_id, payload.text,
        payload.target_member_ids, actor=current,
    )


@router.patch("/comments/{comment_id}", response_model=FeedCommentOut)
async def update_feed_comment(
    comment_id: int, payload: FeedCommentWrite, db: DbSession, current: CurrentMember
) -> FeedCommentOut:
    return await FeedCommentService(db).update(
        comment_id, payload.text, payload.target_member_ids, actor=current
    )


@router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_feed_comment(comment_id: int, db: DbSession, current: CurrentMember) -> None:
    await FeedCommentService(db).delete(comment_id, actor=current)


# 랭크 변동 이벤트 — 서버가 경기 등록/삭제 때마다 계산·저장한 스냅샷 중 실제 변동이
# 있었던 것만 내려준다(피드 카드용). 저장은 서버 내부(matches 서비스 훅)에서만 일어난다.
# 옛 경로(/feed/rank-snapshots)는 배포가 어긋나는 순간을 위해 별칭으로 남겨 둔다(요청:
# API URL도 통일). 프론트가 모두 새 경로를 쓰게 된 뒤 한참 지나면 지워도 된다.
@router.get("/ranking-shifts", response_model=list[RankingShiftOut])
@router.get("/rank-snapshots", response_model=list[RankingShiftOut], include_in_schema=False)
async def list_ranking_shifts(
    db: DbSession, current: CurrentMember, limit: int = Query(default=100, le=500),
) -> list[RankingShiftOut]:
    return await RankingShiftService(db).list_events(limit)


# 지금 바로 하루치 집계를 돌린다(요청: 제어판에 "현재 랭킹 집계하기") — 스케줄러가 아침에
# 하는 것과 똑같은 일이다. 아침을 기다리지 않고 확인하고 싶을 때, 그리고 스케줄러가 정말
# 도는지 눈으로 보고 싶을 때 쓴다. recompute_daily는 순위표가 그대로면 아무것도 남기지
# 않으므로 여러 번 눌러도 카드가 쌓이지 않는다.
@router.post("/ranking-shifts/recompute")
async def recompute_ranking_shifts(db: DbSession, current: CurrentAdmin) -> RankingRecomputeResult:
    from app.main import _rank_entries_computer

    service = RankingShiftService(db)
    before = await service.latest_snapshot_at()
    await service.recompute_daily(await _rank_entries_computer(db))
    after = await service.latest_snapshot_at()
    # 새 스냅샷이 남았나 — 안 남았다면 순위표가 그대로였다는 뜻이고, 그것도 알려 줘야
    # 사람이 "안 돌았나?"로 오해하지 않는다.
    return RankingRecomputeResult(changed=after is not None and after != before)


# 순위 기준선 다시 깔기 — 제어판에서 손으로 누르는 1회용(요청). 변동 없이 저장되므로
# 피드 목록에는 안 뜨고, 다음 아침 재집계가 이 기준선과 비교해 변동을 낸다.
@router.post("/ranking-shifts/seed")
@router.post("/rank-snapshots/seed", include_in_schema=False)
async def reseed_ranking_shifts(db: DbSession, current: CurrentAdmin) -> dict[str, int]:
    from app.main import _rank_entries_computer

    return await RankingShiftService(db).reseed_now(await _rank_entries_computer(db))
