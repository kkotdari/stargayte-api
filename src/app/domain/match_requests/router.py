from fastapi import APIRouter, Query

from app.api.deps import CurrentMember, DbSession
from app.domain.match_requests.schemas import (
    MatchRequestCreate,
    MatchRequestInboxOut,
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


# 내가 언급된 안 읽은 요청 알림(앱 열 때 인박스 팝업용).
@router.get("/inbox", response_model=MatchRequestInboxOut)
async def match_request_inbox(db: DbSession, current: CurrentMember) -> MatchRequestInboxOut:
    return await MatchRequestService(db).list_inbox(actor=current)


# 인박스 팝업을 닫으면 내 안 읽은 알림을 모두 읽음 처리한다.
@router.post("/inbox/read")
async def read_match_request_inbox(db: DbSession, current: CurrentMember) -> dict[str, bool]:
    await MatchRequestService(db).mark_inbox_read(actor=current)
    return {"ok": True}


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


# 대결이 성사되면 작성자 본인/운영자가 "성사됨"으로 완료 처리한다(목록에서 사라짐).
@router.delete("/{request_id}")
async def complete_match_request(
    request_id: int, db: DbSession, current: CurrentMember
) -> dict[str, bool]:
    await MatchRequestService(db).complete_request(request_id, actor=current)
    return {"ok": True}
