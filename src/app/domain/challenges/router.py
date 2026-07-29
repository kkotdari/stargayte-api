from fastapi import APIRouter, status

from app.api.deps import CurrentAdmin, CurrentMember, DbSession
from app.domain.challenges.schemas import (
    ChallengeCreate,
    ChallengeListOut,
    ChallengeOut,
    ChallengeRescheduleIn,
    ChallengeRespondIn,
    ChallengeResultIn,
    ChallengeRevengeIn,
)
from app.domain.challenges.service import ChallengeService

router = APIRouter(prefix="/challenges", tags=["challenges"])


@router.get("", response_model=ChallengeListOut)
async def list_challenges(db: DbSession, current: CurrentMember) -> ChallengeListOut:
    items = await ChallengeService(db).list_challenges(actor=current)
    return ChallengeListOut(items=items)


@router.get("/pending-for-me", response_model=ChallengeListOut)
async def get_pending_for_me(db: DbSession, current: CurrentMember) -> ChallengeListOut:
    items = await ChallengeService(db).get_pending_for_me(actor=current)
    return ChallengeListOut(items=items)


@router.get("/result-pending-for-me", response_model=ChallengeListOut)
async def get_result_pending_for_me(db: DbSession, current: CurrentMember) -> ChallengeListOut:
    items = await ChallengeService(db).get_result_pending_for_me(actor=current)
    return ChallengeListOut(items=items)


@router.post("", response_model=ChallengeOut)
async def create_challenge(
    payload: ChallengeCreate, db: DbSession, current: CurrentMember
) -> ChallengeOut:
    return await ChallengeService(db).create_challenge(payload, actor=current)


@router.post("/{challenge_id}/respond", response_model=ChallengeOut)
async def respond_to_challenge(
    challenge_id: int, payload: ChallengeRespondIn, db: DbSession, current: CurrentMember
) -> ChallengeOut:
    return await ChallengeService(db).respond(
        challenge_id, payload.response, actor=current,
        scheduled_date=payload.scheduled_date, scheduled_time_note=payload.scheduled_time_note,
        message=payload.message,
    )


@router.patch("/{challenge_id}/schedule", response_model=ChallengeOut)
async def reschedule_challenge(
    challenge_id: int, payload: ChallengeRescheduleIn, db: DbSession, current: CurrentMember
) -> ChallengeOut:
    return await ChallengeService(db).reschedule(
        challenge_id, scheduled_date=payload.scheduled_date,
        scheduled_time_note=payload.scheduled_time_note, actor=current,
    )


@router.post("/{challenge_id}/result", response_model=ChallengeOut)
async def enter_challenge_result(
    challenge_id: int, payload: ChallengeResultIn, db: DbSession, current: CurrentMember
) -> ChallengeOut:
    return await ChallengeService(db).enter_result(
        challenge_id, payload.winner_side, actor=current,
        scheduled_date=payload.scheduled_date,
    )


# 완료된 대결에서 패배한 쪽의 재대결(설욕전). 취소/연기/재신청 엔드포인트는 제거됐다 —
# 취소/미실시/거절은 모두 폐기(휴지통)로 통합됐고, 재신청은 없앴다.
@router.post("/{challenge_id}/revenge", response_model=ChallengeOut)
async def revenge_challenge(
    challenge_id: int, payload: ChallengeRevengeIn, db: DbSession, current: CurrentMember
) -> ChallengeOut:
    return await ChallengeService(db).revenge_challenge(
        challenge_id, actor=current, scheduled_date=payload.scheduled_date,
        scheduled_time_note=payload.scheduled_time_note, message=payload.message,
    )


@router.delete("/{challenge_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_challenge(challenge_id: int, db: DbSession, admin: CurrentAdmin) -> None:
    """너 나와! 완전 삭제 — 운영자 전용. 달린 피드 댓글도 함께 지운다."""
    await ChallengeService(db).delete(challenge_id)
