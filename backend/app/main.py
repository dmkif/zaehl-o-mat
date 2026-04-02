import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import SessionLocal, engine
from app.models import Base

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
    # Create tables on startup (alembic handles proper migrations; this is a safety net)
    Base.metadata.create_all(bind=engine)

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.app_base_url, "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
