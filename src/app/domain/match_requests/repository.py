from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.match_requests.models import (
    MatchRequest,
    MatchRequestTarget,
)


class MatchRequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_unread_targets_for(self, member_pk: int) -> list[MatchRequestTarget]:
        """내가 언급된, 아직 안 읽은(read_at NULL) 살아있는 요청의 알림 대상 행 — 최신 요청부터.
        request(및 그 targets/creator, 각 target.member)는 selectin으로 함께 즉시 로드해
        서비스에서 지연로드 없이 인박스 아이템을 만든다."""
        stmt = (
            select(MatchRequestTarget)
            .join(MatchRequest, MatchRequest.id == MatchRequestTarget.request_id)
            .where(
                MatchRequestTarget.member_pk == member_pk,
                MatchRequestTarget.read_at.is_(None),
                MatchRequest.fulfilled_at.is_(None),
            )
            .options(
                selectinload(MatchRequestTarget.request)
                .selectinload(MatchRequest.targets)
                .selectinload(MatchRequestTarget.member),
                selectinload(MatchRequestTarget.request).selectinload(MatchRequest.creator),
            )
            .order_by(MatchRequestTarget.request_id.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().unique().all())

