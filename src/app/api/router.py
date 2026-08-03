from fastapi import APIRouter

from app.domain.activity.router import router as activity_router
from app.domain.app_version.router import registry_router as app_versions_router
from app.domain.app_version.router import router as app_version_router
from app.domain.auth.router import router as auth_router
from app.domain.challenges.router import router as challenges_router
from app.domain.leagues.router import router as leagues_router
from app.domain.game_results.router import router as game_results_router
from app.domain.members.router import router as members_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(members_router)
api_router.include_router(game_results_router, prefix="/game-results")
api_router.include_router(app_version_router)
api_router.include_router(app_versions_router)
api_router.include_router(challenges_router)
api_router.include_router(activity_router, prefix="/activity")
# 옛 경로(/feed/...) — 프론트와 서버는 따로 배포되므로 새 서버가 먼저 뜨는 동안 아직 옛
# 프론트가 /feed를 부른다(요청: 이름 일괄 변경 — 다만 그 사이에 댓글이 안 열리면 안 된다).
# 같은 라우터를 접두어만 바꿔 한 번 더 매단다. 문서에는 안 싣는다. 프론트가 모두 새 경로를
# 쓰게 된 뒤 한참 지나면 이 한 줄만 지우면 된다.
api_router.include_router(activity_router, prefix="/feed", include_in_schema=False)
api_router.include_router(leagues_router)
