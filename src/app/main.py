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


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, debug=settings.debug)

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
