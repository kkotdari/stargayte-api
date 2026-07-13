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
