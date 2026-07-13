from fastapi import APIRouter

from app.api.deps import CurrentMember, DbSession
from app.domain.env_vars.schemas import AdminPanelUnlockIn, AdminPanelUnlockOut
from app.domain.env_vars.service import EnvVarService

router = APIRouter(prefix="/env-vars", tags=["env-vars"])


# 로그인한 회원 누구나 시도 가능 — 이 엔드포인트 자체가 숨겨진 제어판의 첫 관문이라
# 운영자 전용으로 막을 수 없다(운영자인지 아닌지는 이 관문을 통과해야 알 수 있는
# 화면 안에서 갈린다).
@router.post("/admin-panel/verify", response_model=AdminPanelUnlockOut)
async def verify_admin_panel_password(
    payload: AdminPanelUnlockIn, db: DbSession, _member: CurrentMember
) -> AdminPanelUnlockOut:
    ok = await EnvVarService(db).verify_admin_panel_password(payload.password)
    return AdminPanelUnlockOut(ok=ok)
