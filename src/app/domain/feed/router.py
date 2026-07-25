from fastapi import APIRouter, Query, status

from app.api.deps import CurrentMember, DbSession
from app.domain.feed.schemas import (
    FeedCommentCreate,
    FeedCommentOut,
    FeedCommentWrite,
    FeedTargetType,
    RankShiftCreate,
    RankShiftOut,
)
from app.domain.feed.service import FeedCommentService, RankShiftService

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


@router.get("/rank-shifts", response_model=list[RankShiftOut])
async def list_rank_shifts(
    db: DbSession, current: CurrentMember, limit: int = Query(default=100, le=500),
) -> list[RankShiftOut]:
    return await RankShiftService(db).list_recent(limit)


@router.post("/rank-shifts", response_model=RankShiftOut, status_code=status.HTTP_201_CREATED)
async def create_rank_shift(
    payload: RankShiftCreate, db: DbSession, current: CurrentMember
) -> RankShiftOut:
    return await RankShiftService(db).create(payload)
