from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.challenges.models import Challenge, ChallengeParticipant


class ChallengeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, challenge: Challenge) -> None:
        self._session.add(challenge)

    async def flush(self) -> None:
        await self._session.flush()

    async def get(self, challenge_id: int) -> Challenge | None:
        return await self._session.get(Challenge, challenge_id)

    async def list_all(self) -> list[Challenge]:
        # 최신 도전장이 위로 오도록 — 별도 게시판이라 페이지네이션 없이 전부 내려준다
        # (경기결과처럼 무한히 쌓이는 데이터가 아니라 실제로는 많지 않을 것으로 본다).
        result = await self._session.execute(
            select(Challenge).order_by(Challenge.created_at.desc())
        )
        return list(result.scalars().unique().all())

    async def is_superseded(self, challenge_id: int) -> bool:
        """다른 도전장이 이미 이 id를 reapplied_from_id로 가리키고 있으면(재신청이든
        설욕전이든) True — 같은 원본에서 체인이 두 갈래로 갈라지는 것을 막는다."""
        result = await self._session.execute(
            select(Challenge.id).where(Challenge.reapplied_from_id == challenge_id).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def list_pending_targets_for_member(self, member_pk: int) -> list[ChallengeParticipant]:
        result = await self._session.execute(
            select(ChallengeParticipant).where(
                ChallengeParticipant.side == "target",
                ChallengeParticipant.member_pk == member_pk,
                ChallengeParticipant.response == "pending",
                ChallengeParticipant.notified.is_(False),
            )
        )
        return list(result.scalars().all())
