"""Factory ứng dụng FastAPI cho dashboard (tiến trình RIÊNG, cổng 8100, loopback). Không dùng `main.py`/`app.core`. Snapshot được nạp & xác minh MỘT lần khi khởi động."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import labels
from . import schemas as S
from .router import router
from .service import ApiError
from .settings import Settings, load_settings
from .store import SnapshotStore, SnapshotUnavailable, load_store

LOGGER = logging.getLogger("dashboard")


def _error(status: int, code: str, message_id: str, details: Optional[dict] = None) -> JSONResponse:
    body = S.ErrorResponse(error=S.ErrorBody(code=code, message=labels.TEMPLATES[message_id], details=details))
    return JSONResponse(status_code=status, content=body.model_dump(by_alias=True, exclude_none=True))


def create_app(settings: Optional[Settings] = None, store: Optional[SnapshotStore] = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="RateScope dashboard API (dev)", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.store = store
    app.state.unavailable_reason = None
    if store is None:
        try:
            app.state.store = load_store(settings.serving_root)
        except SnapshotUnavailable as exc:
            app.state.unavailable_reason = exc.reason
            LOGGER.error("snapshot unavailable: %s", exc.reason)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=False, allow_methods=["GET"], allow_headers=[])
    app.include_router(router)

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:                      # noqa: ANN202
        return _error(exc.status, exc.code, exc.message_id, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:  # noqa: ANN202
        return _error(422, "invalid_parameter", "err_invalid_parameter")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:       # noqa: ANN202
        if exc.status_code == 404:
            return _error(404, "not_found", "err_invalid_parameter")
        return _error(exc.status_code, "http_error", "err_internal")

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception) -> JSONResponse:                    # noqa: ANN202
        LOGGER.exception("lỗi không lường: %s", exc.__class__.__name__)                  # chỉ log loại lỗi; không trả stack/đường dẫn cho client
        return _error(500, "internal_error", "err_internal")

    return app
