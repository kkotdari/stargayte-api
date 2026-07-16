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
        # 소프트 삭제(deleted_at)된 건은 아예 빼고 로드한다.
        result = await self._session.execute(
            select(Challenge)
            .where(Challenge.deleted_at.is_(None))
            .order_by(Challenge.created_at.desc())
        )
        return list(result.scalars().unique().all())

    async def is_superseded(self, challenge_id: int) -> bool:
        """다른 (살아있는) 도전장이 이미 이 id를 reapplied_from_id로 가리키고 있으면 True —
        같은 원본에서 재대결 체인이 두 갈래로 갈라지는 것을 막는다. 단 그 자식이 폐기(휴지통)
        됐거나 소프트삭제됐으면 세지 않는다 — 재대결이 버려지면 원래 완료 건이 다시 재대결
        대상이 돼야 하기 때문(요청)."""
        result = await self._session.execute(
            select(Challenge.id).where(
                Challenge.reapplied_from_id == challenge_id,
                Challenge.discarded_at.is_(None),
                Challenge.deleted_at.is_(None),
            ).limit(1)
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

    async def list_result_unnotified_for_member(self, member_pk: int) -> list[ChallengeParticipant]:
        """"결과 입력" 팝업 후보 — 이 회원이 참가한(side 무관) 도전장 중 아직 결과 팝업을
        안 본 참가 행. "예정 일시가 지난 확정 대결 + 결과 미입력"이라는 실제 자격은 상태를
        저장하지 않고 매번 계산하는 도메인 규칙이라(service의 _status_of) 여기서 SQL로
        거르지 않고 서비스 레이어가 마저 거른다."""
        result = await self._session.execute(
            select(ChallengeParticipant).where(
                ChallengeParticipant.member_pk == member_pk,
                ChallengeParticipant.result_notified.is_(False),
            )
        )
        return list(result.scalars().all())
