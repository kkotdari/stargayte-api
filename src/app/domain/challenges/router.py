from fastapi import APIRouter

from app.api.deps import CurrentMember, DbSession
from app.domain.challenges.schemas import (
    ChallengeCreate,
    ChallengeListOut,
    ChallengeOut,
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
        challenge_id, payload.response, actor=current, reason=payload.reason,
        scheduled_at=payload.scheduled_at,
    )


@router.post("/{challenge_id}/result", response_model=ChallengeOut)
async def enter_challenge_result(
    challenge_id: int, payload: ChallengeResultIn, db: DbSession, current: CurrentMember
) -> ChallengeOut:
    return await ChallengeService(db).enter_result(
        challenge_id, payload.winner_side, actor=current
    )


# 완료된 대결에서 패배한 쪽의 재대결(설욕전). 취소/연기/재신청 엔드포인트는 제거됐다 —
# 취소/미실시/거절은 모두 폐기(휴지통)로 통합됐고, 재신청은 없앴다.
@router.post("/{challenge_id}/revenge", response_model=ChallengeOut)
async def revenge_challenge(
    challenge_id: int, payload: ChallengeRevengeIn, db: DbSession, current: CurrentMember
) -> ChallengeOut:
    return await ChallengeService(db).revenge_challenge(
        challenge_id, actor=current, scheduled_at=payload.scheduled_at, message=payload.message
    )
