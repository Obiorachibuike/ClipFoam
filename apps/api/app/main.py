"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.router import api_router
from app.config import settings
from app.errors import AppError
from app.storage import get_storage, temp_dir

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
logger = logging.getLogger("clipfoam")


@asynccontextmanager
async def lifespan(app: FastAPI):
    for problem in settings.validate_runtime():
        logger.warning("Configuration: %s", problem)
    settings.local_storage_path.mkdir(parents=True, exist_ok=True)
    temp_dir("bootstrap")
    try:
        get_storage()
    except Exception as exc:  # noqa: BLE001 - storage misconfiguration should be loud
        logger.error("Storage backend unavailable: %s", exc)
    logger.info(
        "ClipFoam API ready (env=%s, storage=%s, transcription=%s, ai=%s)",
        settings.app_env,
        settings.storage_backend,
        settings.transcription_provider,
        settings.ai_provider,
    )
    worker_thread: threading.Thread | None = None
    if settings.enable_inline_worker:
        # Development convenience only: run one worker loop inside the API
        # process so `uvicorn` alone is enough for a local end-to-end run.
        # Production deployments must run `python -m app.workers.worker`.
        from app.workers.runner import run_worker

        worker_thread = threading.Thread(
            target=run_worker,
            kwargs={"concurrency": 1, "identity": "inline-api"},
            name="clipfoam-inline-worker",
            daemon=True,
        )
        worker_thread.start()
        logger.warning("Inline worker started (CF_ENABLE_INLINE_WORKER=true) — not for production")
    try:
        yield
    finally:
        if worker_thread is not None:
            from app.workers.runner import stop_worker

            stop_worker()
            worker_thread.join(timeout=5)


app = FastAPI(
    title="ClipFoam API",
    description=(
        "Turn long videos into short-form gold. ClipFoam is an open-source, "
        "self-hostable alternative to AI clipping suites."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-TOKEN", "X-Organization-Id", "X-Requested-With"],
    expose_headers=["Content-Disposition"],
    max_age=600,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if settings.is_production:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


@app.middleware("http")
async def request_size_limit(request: Request, call_next):
    content_length = request.headers.get("content-length")
    hard_cap = settings.max_upload_mb * 1024 * 1024 + 8 * 1024 * 1024
    if content_length and content_length.isdigit() and int(content_length) > hard_cap:
        return JSONResponse(
            status_code=413,
            content={
                "error": {
                    "code": "request_too_large",
                    "message": f"Request exceeds the {settings.max_upload_mb} MB upload limit",
                }
            },
        )
    return await call_next(request)


@app.exception_handler(AppError)
async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = []
    for error in exc.errors()[:10]:
        errors.append(
            {
                "field": ".".join(str(part) for part in error.get("loc", []) if part != "body"),
                "message": error.get("msg", "Invalid value"),
            }
        )
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Some fields are invalid",
                "details": {"errors": errors},
            }
        },
    )


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": f"http_{exc.status_code}", "message": message}},
    )


@app.exception_handler(SQLAlchemyError)
async def handle_db_error(_request: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.exception("Database error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "database_error",
                "message": "A database error occurred. The request was rolled back.",
            }
        },
    )


@app.exception_handler(Exception)
async def handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "Something went wrong on our side. The failure was logged.",
            }
        },
    )


app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": "ClipFoam API",
        "version": "1.0.0",
        "docs": "/api/docs",
        "health": f"{settings.api_prefix}/health",
    }
