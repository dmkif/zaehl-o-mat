import asyncio
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.body_limit import BodyLimitMiddleware
from app.forwarded_proto import ForwardedProtoMiddleware
from app.auth import get_current_user
from app.models import User, UserRole
from app.config import settings
from app.database import SessionLocal
from app.error_handlers import install as install_error_handlers
from app.limiter import limiter

logging.basicConfig(level=logging.DEBUG if settings.debug else logging.INFO)
logger = logging.getLogger(__name__)
# httpx logs every request line at INFO; keep request details out of the logs.
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

_SENSITIVE_QUERY = re.compile(r"(?i)([?&](?:code|state|api_?key|key|token|access_token)=)[^&\s\"]*")


class AccessLogRedactionFilter(logging.Filter):
    """Mask OIDC ``code``/``state`` query values in the uvicorn access log (F-010)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _SENSITIVE_QUERY.sub(r"\1[redacted]", a) if isinstance(a, str) else a for a in record.args
            )
        if isinstance(record.msg, str):
            record.msg = _SENSITIVE_QUERY.sub(r"\1[redacted]", record.msg)
        return True


logging.getLogger("uvicorn.access").addFilter(AccessLogRedactionFilter())
# F-027: URLs with credentials in the query must not reach any application log either.
for _h in logging.getLogger().handlers:
    _h.addFilter(AccessLogRedactionFilter())

scheduler = AsyncIOScheduler()


def _run_retention():
    from app.jobs.retention import run_retention
    db = SessionLocal()
    try:
        run_retention(db)
    finally:
        db.close()


async def _run_oil_price():
    """Fetch the oil price; never raises (F-025: start-up and the scheduler must survive)."""
    try:
        from app.services.oil_price import fetch_and_store_oil_price
        db = SessionLocal()
        try:
            await fetch_and_store_oil_price(db)
        finally:
            db.close()
    except Exception as exc:
        logger.error("Oil price job failed: %s", type(exc).__name__)


def warn_if_group_diagnostics() -> None:
    if settings.oidc_group_diagnostics:
        logger.warning(
            "OIDC group diagnostics are ENABLED (OIDC_GROUP_DIAGNOSTICS=true): "
            "group names of rejected sign-ins are written to the log")


@asynccontextmanager
async def lifespan(app: FastAPI):
    warn_if_group_diagnostics()
    # Fetch the oil price right away, but in the background: an unreachable or
    # slow source must neither delay nor fail start-up (F-025).
    oil_task = asyncio.create_task(_run_oil_price())

    # Schedule jobs
    scheduler.add_job(_run_retention, CronTrigger(hour=3, minute=0), id="retention")
    scheduler.add_job(_run_oil_price, CronTrigger(hour=6, minute=0), id="oil_price")
    scheduler.start()
    logger.info("Scheduler started")

    yield

    oil_task.cancel()
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")


app = FastAPI(
    title="Zähl-O-Mat API",
    version="0.1.0",
    lifespan=lifespan,
    # F-031: API docs only on explicit opt-in (ENABLE_API_DOCS=true).
    docs_url="/api/docs" if settings.enable_api_docs else None,
    redoc_url="/api/redoc" if settings.enable_api_docs else None,
    openapi_url="/api/openapi.json" if settings.enable_api_docs else None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(BodyLimitMiddleware)  # F-012: bulk-scan body size, before the form is parsed
app.add_middleware(ForwardedProtoMiddleware)  # https behind the proxy, only from TRUSTED_PROXIES


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    # FastAPI's default echoes the rejected input, which cannot be serialized for
    # NaN/Infinity (-> 500) and would reflect arbitrary client data. Keep type/loc/msg.
    detail = [{"type": e["type"], "loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": detail})


install_error_handlers(app)  # F-035: DB outage -> 503, generic 500 (see app/error_handlers.py)


# The Vite dev server origin is only trusted in debug mode — never in production.
_cors_origins = [settings.app_base_url]
if settings.debug:
    _cors_origins.append("http://localhost:5173")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)


_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    # F-046: accept only short, harmless ids; anything else is replaced.
    req_id = request.headers.get("x-request-id", "")
    if not _REQUEST_ID_RE.fullmatch(req_id):
        req_id = str(uuid.uuid4())
    response = await call_next(request)
    response.headers["x-request-id"] = req_id
    return response

# Routers
from app.routers import auth, properties, meters, readings, ocr, dashboard, admin, uploads, oil_deliveries  # noqa: E402

app.include_router(auth.router, prefix="/api")
app.include_router(properties.router, prefix="/api")
app.include_router(meters.router, prefix="/api")
app.include_router(readings.router, prefix="/api")
app.include_router(ocr.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(uploads.router, prefix="/api")
app.include_router(oil_deliveries.router, prefix="/api")

# Ensure upload directory exists
uploads_dir = Path(settings.upload_path)
uploads_dir.mkdir(parents=True, exist_ok=True)


from fastapi import Response  # noqa: E402
from app.database import get_db  # noqa: E402
from sqlalchemy import text  # noqa: E402
import httpx as _httpx  # noqa: E402
from app.services import llm_client as _llm_client  # noqa: E402


@app.get("/api/health/live", tags=["health"])
def health_live():
    """Liveness probe: process is up and can serve requests.

    Deliberately does NOT check the database — restarting the pod does not fix
    a DB outage, so a dead DB must fail readiness, never liveness.
    """
    return {"status": "ok"}


def _db_ok() -> bool:
    db = None
    try:
        db = next(get_db())
        db.execute(text("SELECT 1"))
        return True
    except Exception:  # nosec B110 - Constitution Principle II: any DB failure means "not ready", never a crash
        return False
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:  # nosec B110
                pass


@app.get("/api/health", tags=["health"])
def health(response: Response):
    """Readiness probe, anonymous: ``{"status": ...}`` only (F-031).

    Depends on DB and scheduler alone. Optional services (OCR, gateway,
    Ollama) are never contacted here: a slow GPU host must not flip pod
    readiness (probe timeout) nor can anonymous callers trigger outbound calls.
    """
    core_ok = _db_ok() and scheduler.running
    if not core_ok:
        response.status_code = 503
    return {"status": "ok" if core_ok else "degraded"}


_DETAILS_TTL_S = 30.0
_details_cache: dict = {}


def _external_checks() -> dict:
    """OCR / LLM reachability, cached for _DETAILS_TTL_S so admins cannot amplify outbound calls."""
    now = time.monotonic()
    hit = _details_cache.get("ext")
    if hit and now - hit[0] < _DETAILS_TTL_S:
        return hit[1]

    ocr_ok = False
    if settings.ocr_url:
        try:
            r = _httpx.get(f"{settings.ocr_url}/health", timeout=3.0)
            ocr_ok = r.status_code == 200
        except Exception:  # nosec B110 - optional subsystem, absence must not crash health check
            pass

    llm_ok = False
    llm_model: str | None = None
    if _llm_client.using_gateway():
        llm_model = _llm_client.get_profile().name
        llm_ok = _llm_client.gateway_healthy()
    elif settings.ollama_url:
        llm_model = settings.ollama_model
        try:
            r = _httpx.get(f"{settings.ollama_url}/api/tags", timeout=3.0)
            llm_ok = r.status_code == 200
        except Exception:  # nosec B110 - optional subsystem, absence must not crash health check
            pass

    result = {"ocr": ocr_ok, "llm": llm_ok, "llm_model": llm_model}
    _details_cache["ext"] = (now, result)
    return result


@app.get("/api/health/details", tags=["health"])
def health_details(response: Response, user: User = Depends(get_current_user)):
    """Subsystem state for signed-in users (status footer): DB, scheduler, OCR, LLM.

    Version and model name are only returned to admins. OCR/LLM do not affect status.
    """
    db_ok = _db_ok()
    scheduler_ok = scheduler.running
    core_ok = db_ok and scheduler_ok
    if not core_ok:
        response.status_code = 503
    ext = _external_checks()
    body = {
        "status": "ok" if core_ok else "degraded",
        "db": db_ok,
        "scheduler": scheduler_ok,
        "ocr": ext["ocr"],
        "llm": ext["llm"],
    }
    if user.role in (UserRole.superadmin, UserRole.admin):
        body["version"] = app.version
        body["llm_model"] = ext["llm_model"]
    return body
