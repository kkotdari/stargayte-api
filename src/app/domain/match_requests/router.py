from fastapi import APIRouter, Query

from app.api.deps import CurrentMember, DbSession
from app.domain.match_requests.schemas import (
    MatchRequestCreate,
    MatchRequestListOut,
    MatchRequestOut,
)
from app.domain.match_requests.service import MatchRequestService

router = APIRouter(prefix="/match-requests", tags=["match-requests"])


@router.get("", response_model=MatchRequestListOut)
async def list_match_requests(
    db: DbSession,
    current: CurrentMember,
    page: int = Query(default=0, ge=0),
) -> MatchRequestListOut:
    return await MatchRequestService(db).list_requests(actor=current, page=page)


@router.post("", response_model=MatchRequestOut)
async def create_match_request(
    payload: MatchRequestCreate, db: DbSession, current: CurrentMember
) -> MatchRequestOut:
    return await MatchRequestService(db).create_request(
        payload.text, payload.target_member_ids, actor=current
    )


@router.post("/{request_id}/recommend", response_model=MatchRequestOut)
async def toggle_recommend(
    request_id: int, db: DbSession, current: CurrentMember
) -> MatchRequestOut:
    return await MatchRequestService(db).toggle_recommend(request_id, actor=current)


# "들어주기"로 도전장을 보낸 뒤 요청을 목록에서 내린다(작성자 본인은 호출 불가 — 프론트에서
# 도전장 전송 성공 후 호출).
@router.post("/{request_id}/fulfill")
async def fulfill_match_request(
    request_id: int, db: DbSession, current: CurrentMember
) -> dict[str, bool]:
    await MatchRequestService(db).fulfill(request_id, actor=current)
    return {"ok": True}


# 작성자 본인/운영자가 요청을 내린다.
@router.delete("/{request_id}")
async def delete_match_request(
    request_id: int, db: DbSession, current: CurrentMember
) -> dict[str, bool]:
    await MatchRequestService(db).delete_request(request_id, actor=current)
    return {"ok": True}
