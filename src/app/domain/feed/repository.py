from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.feed.models import FeedComment


class FeedCommentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
