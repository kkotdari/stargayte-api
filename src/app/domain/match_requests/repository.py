from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.match_requests.models import (
    MatchRequest,
    MatchRequestRecommend,
    MatchRequestTarget,
)


class MatchRequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, request: MatchRequest) -> None:
        self._session.add(request)

    async def flush(self) -> None:
        await self._session.flush()

    async def get(self, request_id: int) -> MatchRequest | None:
        # select로 로드해야 selectin 관계(targets/recommends/creator 및 각 target.member)가
        # 비동기 컨텍스트에서 함께 즉시 로드된다(session.get의 지연로드는 _to_out에서
        # MissingGreenlet을 낸다).
        result = await self._session.execute(
            select(MatchRequest).where(MatchRequest.id == request_id)
        )
        return result.scalar_one_or_none()

    async def list_all_alive(self) -> list[MatchRequest]:
        """살아있는 요청 전부(작성자·지목 포함) — 같은 구성원의 요청이 이미 있는지 확인용."""
        result = await self._session.execute(
            select(MatchRequest).where(MatchRequest.fulfilled_at.is_(None))
        )
        return list(result.scalars().unique().all())

    async def count_alive(self) -> int:
        total = await self._session.scalar(
            select(func.count())
            .select_from(MatchRequest)
            .where(MatchRequest.fulfilled_at.is_(None))
        )
        return int(total or 0)

    async def list_page(self, *, page: int, page_size: int) -> list[MatchRequest]:
        """살아있는(fulfilled 안 된) 요청을 추천 많은 순 → 먼저 등록된 순으로 한 페이지 반환.
        추천 수는 추천 테이블을 요청별로 집계한 서브쿼리로 정렬 기준만 잡고, 실제 개수/내가
        추천했는지는 selectin으로 함께 로드되는 recommends 관계에서 service가 센다."""
        count_sub = (
            select(
                MatchRequestRecommend.request_id.label("request_id"),
                func.count().label("rc"),
            )
            .group_by(MatchRequestRecommend.request_id)
            .subquery()
        )
        stmt = (
            select(MatchRequest)
            .outerjoin(count_sub, count_sub.c.request_id == MatchRequest.id)
            .where(MatchRequest.fulfilled_at.is_(None))
            .order_by(
                func.coalesce(count_sub.c.rc, 0).desc(),
                MatchRequest.created_at.asc(),
                MatchRequest.id.asc(),
            )
            .limit(page_size)
            .offset(page * page_size)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().unique().all())

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

    async def get_recommend(
        self, request_id: int, member_pk: int
    ) -> MatchRequestRecommend | None:
        result = await self._session.execute(
            select(MatchRequestRecommend).where(
                MatchRequestRecommend.request_id == request_id,
                MatchRequestRecommend.member_pk == member_pk,
            )
        )
        return result.scalar_one_or_none()

    def add_recommend(self, recommend: MatchRequestRecommend) -> None:
        self._session.add(recommend)

    async def delete_recommend(self, recommend: MatchRequestRecommend) -> None:
        await self._session.delete(recommend)
