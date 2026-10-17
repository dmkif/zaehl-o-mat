"""Rig-only ASGI entrypoint (feature 004). Wraps the unmodified backend app.

The backend is started as `uvicorn rig_app:app --app-dir <backend dir>` with PYTHONPATH containing
security/rig. When RIG_DISABLE_LIMITER=1 (rig default) the slowapi limiter is switched off before
serving, so the 5/20/30-per-minute limits do not slow down or distort unrelated experiments
(429 waits, restarts for a fresh window). Only the rate-limit experiments (E-A07-001) restart the
backend with RIG_DISABLE_LIMITER unset, i.e. with the real limiter. No backend/app file is touched.
"""
import os

from app.main import app  # noqa: F401  (re-exported for uvicorn)
from app.limiter import limiter

if os.environ.get("RIG_DISABLE_LIMITER") == "1":
    limiter.enabled = False

# Debug aid for hang analysis (B-32): `kill -USR1 <backend pid>` dumps all thread stacks to the backend log.
import faulthandler
import signal

faulthandler.register(signal.SIGUSR1, all_threads=True)
