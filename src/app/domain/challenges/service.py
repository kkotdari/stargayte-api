from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.domain.challenges.models import Challenge, ChallengeParticipant
from app.domain.challenges.repository import ChallengeRepository
from app.domain.challenges.schemas import (
    ChallengeAuthor,
    ChallengeCreate,
    ChallengeOut,
    ChallengeOwnMemberOut,
    ChallengeTargetOut,
)
from app.domain.matches.models import Match
from app.domain.members.models import Member
from app.domain.members.repository import MemberRepository


def _status_of(challenge: Challenge) -> str:
    if challenge.canceled_at is not None:
        return "canceled"
    responses = [p.response for p in challenge.participants if p.side == "target"]
    if any(r == "rejected" for r in responses):
        return "rejected"
    if responses and all(r == "accepted" for r in responses):
        return "confirmed"
    return "pending"


def to_challenge_out(challenge: Challenge) -> ChallengeOut:
    # 응답 한마디(수락/거절 모두)는 전체 공개다 — 요청자가 아니어도 누구나 볼 수 있다
    # (예전엔 요청자만 봤지만, 요청에 따라 제한을 없앴다).
    targets = [p for p in challenge.participants if p.side == "target"]
    own_members = [
        p for p in challenge.participants if p.side == "creator" and p.member_pk != challenge.created_by
    ]
    return ChallengeOut(
        id=challenge.id,
        matchType=challenge.match_type,
        scheduledAt=challenge.scheduled_at,
        message=challenge.message,
        status=_status_of(challenge),
        createdBy=ChallengeAuthor(id=challenge.creator.id, nickname=challenge.creator.nickname),
        targets=[
            ChallengeTargetOut(
                memberId=p.member.id,
                nickname=p.member.nickname,
                battletag=p.member.battletag,
                avatar=p.member.avatar_url,
                response=p.response,
                responseMessage=p.response_message,
            )
            for p in targets
        ],
        ownMembers=[
            ChallengeOwnMemberOut(
                memberId=p.member.id,
                nickname=p.member.nickname,
                battletag=p.member.battletag,
                avatar=p.member.avatar_url,
            )
            for p in own_members
        ],
        resultMatchId=challenge.result_match_id,
        createdAt=challenge.created_at,
    )


class ChallengeService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ChallengeRepository(session)
        self._member_repo = MemberRepository(session)

    async def list_challenges(self, *, actor: Member) -> list[ChallengeOut]:
        challenges = await self._repo.list_all()
        return [to_challenge_out(c) for c in challenges]

    async def create_challenge(self, payload: ChallengeCreate, *, actor: Member) -> ChallengeOut:
        target_members: list[Member] = []
        for member_id in payload.target_member_ids:
            m = await self._member_repo.get_by_login_id(member_id)
            if m is None:
                raise NotFoundError(f"존재하지 않는 회원입니다: {member_id}")
            if m.pk == actor.pk:
                raise ValidationError("자기 자신을 지목할 수 없습니다.")
            target_members.append(m)

        # 본인은 자동 포함(뺄 수 없음)이라 own_team_member_ids엔 "본인 제외 나머지 내
        # 팀원"만 들어온다.
        own_members: list[Member] = []
        for member_id in payload.own_team_member_ids:
            m = await self._member_repo.get_by_login_id(member_id)
            if m is None:
                raise NotFoundError(f"존재하지 않는 회원입니다: {member_id}")
            if m.pk == actor.pk:
                raise ValidationError("본인은 이미 자동으로 포함돼 있습니다.")
            own_members.append(m)

        # 폼에서 직접 고르지 않고 양쪽 인원수로 정한다: 양쪽 다 1명(나 혼자 vs 상대
        # 1명)이면 1:1, 그 외(어느 한쪽이라도 2명 이상)엔 팀전.
        match_type = (
            "0101" if len(target_members) == 1 and len(own_members) == 0 else "0102"
        )

        challenge = Challenge(
            match_type=match_type,
            scheduled_at=payload.scheduled_at,
            message=payload.message,
            created_by=actor.pk,
            updated_by=actor.pk,
        )
        challenge.participants = (
            [ChallengeParticipant(member_pk=actor.pk, side="creator")]
            + [ChallengeParticipant(member_pk=m.pk, side="creator") for m in own_members]
            + [ChallengeParticipant(member_pk=m.pk, side="target") for m in target_members]
        )
        self._repo.add(challenge)
        await self._repo.flush()
        await self._session.commit()
        await self._session.refresh(challenge, attribute_names=["creator", "participants"])
        return to_challenge_out(challenge)

    async def get_pending_for_me(self, *, actor: Member) -> list[ChallengeOut]:
        pending = await self._repo.list_pending_targets_for_member(actor.pk)
        challenges: list[Challenge] = []
        for p in pending:
            p.notified = True
            challenge = await self._repo.get(p.challenge_id)
            if challenge is not None:
                challenges.append(challenge)
        await self._session.commit()
        return [to_challenge_out(c) for c in challenges]

    async def respond(
        self,
        challenge_id: int,
        response: str,
        *,
        actor: Member,
        reason: str | None = None,
        scheduled_at: datetime | None = None,
    ) -> ChallengeOut:
        challenge = await self._repo.get(challenge_id)
        if challenge is None:
            raise NotFoundError("도전장을 찾을 수 없습니다.")
        target = next(
            (p for p in challenge.participants if p.side == "target" and p.member_pk == actor.pk), None
        )
        if target is None:
            raise ForbiddenError("이 도전장에 지목되지 않았습니다.")
        if challenge.canceled_at is not None:
            raise ValidationError("취소된 도전장입니다.")
        if target.response != "pending":
            raise ValidationError("이미 응답한 도전장입니다.")
        # 요청자가 "시간 지정"을 끄고 보낸(scheduled_at=None) 도전장은 "상대가 정해도
        # 된다"는 뜻이라, 수락하는 이 시점에 상대가 직접 정하게 한다 — 안 그러면 시간이
        # 영원히 안 채워진 채 "승락" 상태로 박제된다(요청: "도전자/상대 모두 시간을
        # 지정하지 않았는데 수락이 된 경우가 있네 이러면 안되는데"). 이미 시간이 정해진
        # 도전장은 응답하는 쪽이 바꿀 수 없으므로 여기서 들어온 값은 무시한다.
        if response == "accepted" and challenge.scheduled_at is None:
            if scheduled_at is None:
                raise ValidationError("일시가 정해지지 않은 도전장이에요 — 수락하며 시간을 정해주세요.")
            challenge.scheduled_at = scheduled_at
            challenge.updated_by = actor.pk
        target.response = response
        target.responded_at = datetime.now(UTC)
        # 이제 수락에도 한마디를 받는다(요청: "편지지에 수락/거절 한줄 메시지 필수화")
        # — 응답 종류와 무관하게 그대로 저장한다.
        target.response_message = reason
        await self._session.commit()
        await self._session.refresh(challenge, attribute_names=["participants"])
        return to_challenge_out(challenge)

    async def cancel_challenge(self, challenge_id: int, *, actor: Member) -> ChallengeOut:
        """요청자(도전자)가 확정 전에 스스로 취소한다 — 이미 전원이 승락(confirmed)한
        뒤에는 취소할 수 없다(경기가 이미 잡힌 것으로 본다)."""
        challenge = await self._repo.get(challenge_id)
        if challenge is None:
            raise NotFoundError("도전장을 찾을 수 없습니다.")
        if challenge.created_by != actor.pk:
            raise ForbiddenError("요청자만 취소할 수 있습니다.")
        if _status_of(challenge) != "pending":
            raise ValidationError("확정되었거나 이미 처리된 도전장은 취소할 수 없습니다.")
        challenge.canceled_at = datetime.now(UTC)
        challenge.updated_by = actor.pk
        await self._session.commit()
        await self._session.refresh(challenge, attribute_names=["participants"])
        return to_challenge_out(challenge)

    async def reapply_challenge(
        self,
        challenge_id: int,
        *,
        actor: Member,
        scheduled_at: datetime | None = None,
        message: str | None = None,
    ) -> ChallengeOut:
        """거절된 도전장을 재신청 — 지목된 쪽 전원의 응답을 pending으로 되돌리고, 시간/
        메모를 원하면 이 참에 고쳐서 다시 보낸다(안 넘기면 기존 값 그대로)."""
        challenge = await self._repo.get(challenge_id)
        if challenge is None:
            raise NotFoundError("도전장을 찾을 수 없습니다.")
        if challenge.created_by != actor.pk:
            raise ForbiddenError("요청자만 재신청할 수 있습니다.")
        if _status_of(challenge) != "rejected":
            raise ValidationError("거절된 도전장만 재신청할 수 있습니다.")
        if scheduled_at is not None:
            challenge.scheduled_at = scheduled_at
        if message is not None:
            challenge.message = message
        challenge.updated_by = actor.pk
        for p in challenge.participants:
            if p.side != "target":
                continue
            p.response = "pending"
            p.response_message = None
            p.responded_at = None
            p.notified = False
        await self._session.commit()
        await self._session.refresh(challenge, attribute_names=["participants"])
        return to_challenge_out(challenge)

    async def attach_result(self, challenge_id: int, match_id: int, *, actor: Member) -> ChallengeOut:
        # 예전엔 도전장 참가자만 결과를 연결할 수 있었지만, 리플레이 등록(및 그 안의
        # 게임아이디 매핑)을 누구나 할 수 있게 열면서 이 연결도 참가자로 좁힐 이유가
        # 없어졌다 — 인증된 회원이면 누구나 연결할 수 있다.
        challenge = await self._repo.get(challenge_id)
        if challenge is None:
            raise NotFoundError("도전장을 찾을 수 없습니다.")
        if _status_of(challenge) != "confirmed":
            raise ValidationError("전원이 승락한 도전장만 결과를 연결할 수 있습니다.")
        match = await self._session.get(Match, match_id)
        if match is None:
            raise NotFoundError("경기결과를 찾을 수 없습니다.")
        challenge.result_match_id = match_id
        challenge.updated_by = actor.pk
        await self._session.commit()
        await self._session.refresh(challenge, attribute_names=["participants"])
        return to_challenge_out(challenge)
