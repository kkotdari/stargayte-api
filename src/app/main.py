from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import settings
from app.core.exceptions import (
    AppError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from app.core.logging import configure_logging

configure_logging()


async def _ensure_schema() -> None:
    """스키마가 없으면 생성한다 — 마이그레이션 없이 create_all 하나로 관리.

    기존 DB(테이블이 이미 있는 경우)에는 아무 것도 하지 않으므로 데이터가 보존된다.
    """
    # 모든 도메인 모델을 임포트해 Base.metadata에 테이블을 등록한다.
    from app.db.base import Base
    from app.db.session import engine
    from app.domain.app_version import models as _app_version_models  # noqa: F401
    from app.domain.auth import models as _auth_models  # noqa: F401
    from app.domain.challenges import models as _challenges_models  # noqa: F401
    from app.domain.env_vars import models as _env_vars_models  # noqa: F401
    from app.domain.match_requests import models as _match_requests_models  # noqa: F401
    from app.domain.matches import models as _matches_models  # noqa: F401
    from app.domain.members import models as _members_models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await _ensure_schema()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=_lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _register_exception_handlers(app)

    upload_root = Path(settings.storage_local_root)
    upload_root.mkdir(parents=True, exist_ok=True)
    # 랭킹 카톡 공유 기능 제거(요청)에 따른 잔여물 정리 — 공유 카드 썸네일이 쌓이던
    # share/ 하위를 부팅 시 비운다. 멱등이라 이미 비어 있으면 아무 일도 없다.
    import shutil
    shutil.rmtree(upload_root / "share", ignore_errors=True)
    app.mount(settings.storage_url_path, StaticFiles(directory=upload_root), name="uploads")

    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _register_exception_handlers(app: FastAPI) -> None:
    status_by_error = {
        NotFoundError: 404,
        ConflictError: 409,
        ValidationError: 400,
        UnauthorizedError: 401,
        ForbiddenError: 403,
    }

    for error_cls, status_code in status_by_error.items():

        def make_handler(code: int):
            async def handler(_request: Request, exc: AppError) -> JSONResponse:
                return JSONResponse(status_code=code, content={"detail": exc.message})

            return handler

        app.add_exception_handler(error_cls, make_handler(status_code))

    async def fallback_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": exc.message})

    app.add_exception_handler(AppError, fallback_handler)


app = create_app()
