from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.feed.models import FeedComment


class FeedCommentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[FeedComment]:
        """댓글 전부 — 피드가 목록을 부를 때 한 번에 같이 받아 가려고 쓴다(요청).

        카드마다 따로 부르면 요청이 카드 수만큼 나가고, 무엇보다 답이 제각각 도착하면서
        카드 키가 뒤늦게 자라 피드의 스크롤 자리가 밀린다. 댓글은 한 줄(최대 50자)짜리라
        전부 합쳐도 가벼워서 한 번에 주는 편이 낫다."""
        stmt = (
            select(FeedComment)
            .options(selectinload(FeedComment.mentions), selectinload(FeedComment.creator))
            .order_by(FeedComment.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_by_target(self, target_type: str, target_id: int) -> list[FeedComment]:
        stmt = (
            select(FeedComment)
            .where(FeedComment.target_type == target_type, FeedComment.target_id == target_id)
            .options(selectinload(FeedComment.mentions), selectinload(FeedComment.creator))
            .order_by(FeedComment.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def get(self, comment_id: int) -> FeedComment | None:
        stmt = (
            select(FeedComment)
            .where(FeedComment.id == comment_id)
            .options(selectinload(FeedComment.mentions), selectinload(FeedComment.creator))
        )
        return await self._session.scalar(stmt)
