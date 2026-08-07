from fastapi import APIRouter

from app.domain.activity.router import router as activity_router
from app.domain.app_version.router import registry_router as app_versions_router
from app.domain.app_version.router import router as app_version_router
from app.domain.auth.router import router as auth_router
from app.domain.challenges.router import router as challenges_router
from app.domain.leagues.router import router as leagues_router
from app.domain.game_results.router import router as game_results_router
from app.domain.members.router import router as members_router
from app.domain.schedules.router import router as schedules_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(members_router)
api_router.include_router(game_results_router, prefix="/game-results")
api_router.include_router(app_version_router)
api_router.include_router(app_versions_router)
api_router.include_router(challenges_router)
api_router.include_router(schedules_router)
# 활동은 /activities다 — 목록이 GET /api/activities로 끝난다(요청). 다른 자원들과 같은
# 복수형이고, 목록을 받는 데 /feed 같은 꼬리말이 붙지 않는다.
api_router.include_router(activity_router, prefix="/activities")
api_router.include_router(leagues_router)
