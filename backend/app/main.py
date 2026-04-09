import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.config import settings
from app.database import SessionLocal
from app.limiter import limiter

logging.basicConfig(level=logging.DEBUG if settings.debug else logging.INFO)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def _run_retention():
    from app.jobs.retention import run_retention
    db = SessionLocal()
    try:
        run_retention(db)
    finally:
        db.close()


async def _run_oil_price():
    from app.services.oil_price import fetch_and_store_oil_price
    db = SessionLocal()
    try:
        await fetch_and_store_oil_price(db)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fetch oil price immediately so data is available from the first request
    await _run_oil_price()

    # Schedule jobs
    scheduler.add_job(_run_retention, CronTrigger(hour=3, minute=0), id="retention")
    scheduler.add_job(_run_oil_price, CronTrigger(hour=6, minute=0), id="oil_price")
    scheduler.start()
    logger.info("Scheduler started")

    yield

    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")


app = FastAPI(
    title="Zähl-O-Mat API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.app_base_url, "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    req_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    response = await call_next(request)
    response.headers["x-request-id"] = req_id
    return response

# Routers
from app.routers import auth, properties, meters, readings, ocr, dashboard, admin, uploads  # noqa: E402

app.include_router(auth.router, prefix="/api")
app.include_router(properties.router, prefix="/api")
app.include_router(meters.router, prefix="/api")
app.include_router(readings.router, prefix="/api")
app.include_router(ocr.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(uploads.router, prefix="/api")

# Ensure upload directory exists
uploads_dir = Path(settings.upload_path)
uploads_dir.mkdir(parents=True, exist_ok=True)


from fastapi import Response  # noqa: E402
from app.database import get_db  # noqa: E402
from sqlalchemy import text  # noqa: E402
import httpx as _httpx  # noqa: E402


@app.get("/api/health", tags=["health"])
def health(response: Response):
    """Liveness + readiness probe: checks DB, scheduler, OCR and LLM availability."""
    db_ok = False
    try:
        db = next(get_db())
        db.execute(text("SELECT 1"))
        db.close()
        db_ok = True
    except Exception:
        pass

    scheduler_ok = scheduler.running

    # EasyOCR availability (cached import check)
    from app.routers.ocr import _is_easyocr_available
    ocr_ok = _is_easyocr_available()

    # Ollama / LLM reachability
    llm_ok = False
    llm_model: str | None = None
    if settings.ollama_url:
        llm_model = settings.ollama_model
        try:
            r = _httpx.get(f"{settings.ollama_url}/api/tags", timeout=3.0)
            llm_ok = r.status_code == 200
        except Exception:
            pass

    # Core services (DB + scheduler) determine overall health.
    # OCR and LLM are optional — their absence does not degrade status.
    core_ok = db_ok and scheduler_ok
    if not core_ok:
        response.status_code = 503

    return {
        "status": "ok" if core_ok else "degraded",
        "version": app.version,
        "db": db_ok,
        "scheduler": scheduler_ok,
        "ocr": ocr_ok,
        "llm": llm_ok,
        "llm_model": llm_model,
    }
