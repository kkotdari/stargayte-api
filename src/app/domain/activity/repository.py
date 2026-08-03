from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.activity.models import ActivityComment


class ActivityCommentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[ActivityComment]:
        """댓글 전부 — 활동가 목록을 부를 때 한 번에 같이 받아 가려고 쓴다(요청).

        카드마다 따로 부르면 요청이 카드 수만큼 나가고, 무엇보다 답이 제각각 도착하면서
        카드 키가 뒤늦게 자라 활동의 스크롤 자리가 밀린다. 댓글은 한 줄(최대 50자)짜리라
        전부 합쳐도 가벼워서 한 번에 주는 편이 낫다."""
        stmt = (
            select(ActivityComment)
            .options(selectinload(ActivityComment.mentions), selectinload(ActivityComment.creator))
            .order_by(ActivityComment.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_by_target(self, target_type: str, target_id: int) -> list[ActivityComment]:
        stmt = (
            select(ActivityComment)
            .where(ActivityComment.target_type == target_type, ActivityComment.target_id == target_id)
            .options(selectinload(ActivityComment.mentions), selectinload(ActivityComment.creator))
            .order_by(ActivityComment.created_at)
        )
        return list((await self._session.scalars(stmt)).all())

    async def get(self, comment_id: int) -> ActivityComment | None:
        stmt = (
            select(ActivityComment)
            .where(ActivityComment.id == comment_id)
            .options(selectinload(ActivityComment.mentions), selectinload(ActivityComment.creator))
        )
        return await self._session.scalar(stmt)
