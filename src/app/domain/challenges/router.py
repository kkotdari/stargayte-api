from fastapi import APIRouter

from app.api.deps import CurrentMember, DbSession, StorageDep
from app.domain.challenges.schemas import (
    ChallengeAttachResultIn,
    ChallengeCreate,
    ChallengeListOut,
    ChallengeOut,
    ChallengeReapplyIn,
    ChallengeRespondIn,
)
from app.domain.challenges.service import ChallengeService

router = APIRouter(prefix="/challenges", tags=["challenges"])


@router.get("", response_model=ChallengeListOut)
async def list_challenges(db: DbSession, storage: StorageDep, current: CurrentMember) -> ChallengeListOut:
    items = await ChallengeService(db, storage).list_challenges(actor=current)
    return ChallengeListOut(items=items)


@router.get("/pending-for-me", response_model=ChallengeListOut)
async def get_pending_for_me(db: DbSession, storage: StorageDep, current: CurrentMember) -> ChallengeListOut:
    items = await ChallengeService(db, storage).get_pending_for_me(actor=current)
    return ChallengeListOut(items=items)


@router.post("", response_model=ChallengeOut)
async def create_challenge(
    payload: ChallengeCreate, db: DbSession, storage: StorageDep, current: CurrentMember
) -> ChallengeOut:
    return await ChallengeService(db, storage).create_challenge(payload, actor=current)


@router.post("/{challenge_id}/respond", response_model=ChallengeOut)
async def respond_to_challenge(
    challenge_id: int, payload: ChallengeRespondIn, db: DbSession, storage: StorageDep, current: CurrentMember
) -> ChallengeOut:
    return await ChallengeService(db, storage).respond(
        challenge_id, payload.response, actor=current, reason=payload.reason
    )


@router.post("/{challenge_id}/cancel", response_model=ChallengeOut)
async def cancel_challenge(
    challenge_id: int, db: DbSession, storage: StorageDep, current: CurrentMember
) -> ChallengeOut:
    return await ChallengeService(db, storage).cancel_challenge(challenge_id, actor=current)


@router.post("/{challenge_id}/reapply", response_model=ChallengeOut)
async def reapply_challenge(
    challenge_id: int, payload: ChallengeReapplyIn, db: DbSession, storage: StorageDep, current: CurrentMember
) -> ChallengeOut:
    return await ChallengeService(db, storage).reapply_challenge(
        challenge_id, actor=current, scheduled_at=payload.scheduled_at, message=payload.message
    )


@router.post("/{challenge_id}/attach-result", response_model=ChallengeOut)
async def attach_challenge_result(
    challenge_id: int, payload: ChallengeAttachResultIn, db: DbSession, storage: StorageDep, current: CurrentMember
) -> ChallengeOut:
    return await ChallengeService(db, storage).attach_result(challenge_id, payload.match_id, actor=current)
