from fastapi import APIRouter, status

from app.api.deps import CurrentAdmin, CurrentMember, DbSession, StorageDep
from app.domain.challenges.schemas import (
    ChallengeCreate,
    ChallengeListOut,
    ChallengeOut,
    ChallengeRescheduleIn,
    ChallengeRespondIn,
    ChallengeResultIn,
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
    payload: ChallengeCreate, db: DbSession, current: CurrentMember, storage: StorageDep
) -> ChallengeOut:
    # 호출 만들기만 저장소를 함께 받는다 — 편지지 배경 사진이 여기로만 들어온다.
    return await ChallengeService(db, storage).create_challenge(payload, actor=current)


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


@router.post("/{challenge_id}/cancel", response_model=ChallengeOut)
async def cancel_challenge(challenge_id: int, db: DbSession, current: CurrentMember) -> ChallengeOut:
    """너 나와! 취소 — 부른 사람이 성사 전에 스스로 거둬들인다(요청: "호출자가 취소도 가능함").

    삭제(운영자 전용)와 다르다: 기록은 남고 폐기로만 넘어가며, 누가 취소했는지를 함께
    적어 둔다. 활동는 그 값으로 "취소"와 "만료"를 갈라 보여준다.
    """
    return await ChallengeService(db).cancel(challenge_id, actor=current)


@router.delete("/{challenge_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_challenge(challenge_id: int, db: DbSession, admin: CurrentAdmin) -> None:
    """너 나와! 완전 삭제 — 운영자 전용. 달린 활동 댓글도 함께 지운다."""
    await ChallengeService(db).delete(challenge_id)
