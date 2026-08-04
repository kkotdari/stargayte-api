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
# 활동은 /activities다 — 목록이 GET /api/activities로 끝난다(요청). 다른 자원들과 같은
# 복수형이고, 목록을 받는 데 /feed 같은 꼬리말이 붙지 않는다.
api_router.include_router(activity_router, prefix="/activities")
# 옛 경로(/activity/..., /feed/...) — 프론트와 서버는 따로 배포되므로 새 서버가 먼저 뜨는
# 동안 아직 옛 프론트가 옛 경로를 부른다(그 사이에 활동 화면이 죽으면 안 된다). 같은
# 라우터를 접두어만 바꿔 더 매단다. 문서에는 안 싣는다. 프론트가 모두 새 경로를 쓰게 된 뒤
# 한참 지나면 이 두 줄만 지우면 된다.
api_router.include_router(activity_router, prefix="/activity", include_in_schema=False)
api_router.include_router(activity_router, prefix="/feed", include_in_schema=False)
api_router.include_router(leagues_router)
