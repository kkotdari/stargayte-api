from fastapi import APIRouter, Query, status

from app.api.deps import CurrentAdmin, CurrentMember, DbSession
from app.domain.feed.schemas import (
    FeedCommentCreate,
    FeedCommentOut,
    FeedCommentWrite,
    FeedTargetType,
    RankingShiftOut,
)
from app.domain.feed.service import FeedCommentService, RankingShiftService

router = APIRouter(prefix="/feed", tags=["feed"])


@router.get("/comments", response_model=list[FeedCommentOut])
async def list_feed_comments(
    db: DbSession, current: CurrentMember,
    target_type: FeedTargetType = Query(alias="targetType"),
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


# 순위 기준선 다시 깔기 — 제어판에서 손으로 누르는 1회용(요청). 변동 없이 저장되므로
# 피드 목록에는 안 뜨고, 다음 자정 재집계가 이 기준선과 비교해 변동을 낸다.
@router.post("/ranking-shifts/seed")
@router.post("/rank-snapshots/seed", include_in_schema=False)
async def reseed_ranking_shifts(db: DbSession, current: CurrentAdmin) -> dict[str, int]:
    from app.main import _rank_entries_computer

    return await RankingShiftService(db).reseed_now(await _rank_entries_computer(db))
