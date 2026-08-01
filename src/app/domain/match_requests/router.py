from fastapi import APIRouter

from app.api.deps import CurrentMember, DbSession
from app.domain.match_requests.schemas import MatchRequestInboxOut
from app.domain.match_requests.service import MatchRequestService

# 대결 요청 기능은 인박스(언급 알림)만 남았다 — 목록/등록/추천/완료 화면이 없어져 그
# 엔드포인트들을 지웠다. 등록 경로가 없으므로 새 알림은 더 이상 생기지 않고, 이 두 경로는
# 이미 쌓여 있는 알림을 보여주고 읽음 처리하는 용도로만 남는다.
router = APIRouter(prefix="/match-requests", tags=["match-requests"])


# 내가 언급된 안 읽은 요청 알림(앱 열 때 인박스 팝업용).
@router.get("/inbox", response_model=MatchRequestInboxOut)
async def match_request_inbox(db: DbSession, current: CurrentMember) -> MatchRequestInboxOut:
    return await MatchRequestService(db).list_inbox(actor=current)


# 인박스 팝업을 닫으면 내 안 읽은 알림을 모두 읽음 처리한다.
@router.post("/inbox/read")
async def read_match_request_inbox(db: DbSession, current: CurrentMember) -> dict[str, bool]:
    await MatchRequestService(db).mark_inbox_read(actor=current)
    return {"ok": True}


