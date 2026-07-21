from fastapi import APIRouter

from app.domain.app_version.router import registry_router as app_versions_router
from app.domain.app_version.router import router as app_version_router
from app.domain.auth.router import router as auth_router
from app.domain.challenges.router import router as challenges_router
from app.domain.env_vars.router import router as env_vars_router
from app.domain.leagues.router import router as leagues_router
from app.domain.match_requests.router import router as match_requests_router
from app.domain.matches.router import router as matches_router
from app.domain.members.router import router as members_router
from app.domain.settings.router import router as settings_router
from app.domain.share_images.router import router as share_images_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(members_router)
api_router.include_router(matches_router)
api_router.include_router(settings_router)
api_router.include_router(app_version_router)
api_router.include_router(app_versions_router)
api_router.include_router(challenges_router)
api_router.include_router(match_requests_router)
api_router.include_router(env_vars_router)
api_router.include_router(share_images_router)
api_router.include_router(leagues_router)
