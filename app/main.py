"""FastAPI application entrypoint: uvicorn app.main:app"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routers import certificates
from .services.processor import shutdown_executor

logger = logging.getLogger(__name__)

DESCRIPTION = (
    "Submit one request containing many recipients; each valid recipient gets a "
    "PDF certificate rendered from a single template. Generation runs on a Celery "
    "worker - poll the status endpoint for progress, then download individual "
    "PDFs or the batch as a ZIP."
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
    yield
    shutdown_executor()


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url="/redoc" if settings.enable_docs else None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(certificates.router)

    @app.get("/", include_in_schema=False)
    def root():
        return {"service": settings.app_name, "docs": "/docs", "health": "/health"}

    @app.get("/health", tags=["health"], summary="Liveness probe")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
