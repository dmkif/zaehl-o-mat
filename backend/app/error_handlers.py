"""Clean error responses for database failures and unexpected errors (F-035, F-023).

``install(app)`` registers the handlers; ``main.py`` calls it once.  No handler
puts exception text, SQL or connection details into the body - details go to
the log only.

* connection loss / pool exhaustion / driver interface errors -> 503 + Retry-After
* ``DataError`` (value the column cannot hold)                -> 422
* an ``IntegrityError`` nobody handled                        -> 409
* any other database error / unhandled exception              -> generic 500
"""
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import exc as sa_exc

logger = logging.getLogger(__name__)

_UNAVAILABLE = {"detail": "Service temporarily unavailable"}


def _json(status_code: int, detail: str, **headers: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail}, headers=headers or None)


def _unavailable() -> JSONResponse:
    return JSONResponse(status_code=503, content=_UNAVAILABLE, headers={"Retry-After": "5"})


async def _dbapi_error(request: Request, exc: sa_exc.DBAPIError) -> JSONResponse:
    if isinstance(exc, (sa_exc.OperationalError, sa_exc.InterfaceError)) or exc.connection_invalidated:
        logger.error("database unavailable on %s %s: %s", request.method, request.url.path, type(exc.orig).__name__)
        return _unavailable()
    if isinstance(exc, sa_exc.DataError):
        logger.warning("database rejected a value on %s %s: %s", request.method, request.url.path, type(exc.orig).__name__)
        return _json(422, "Invalid input")
    if isinstance(exc, sa_exc.IntegrityError):
        logger.warning("integrity error on %s %s: %s", request.method, request.url.path, type(exc.orig).__name__)
        return _json(409, "Conflict with existing data")
    logger.error("database error on %s %s: %s", request.method, request.url.path, type(exc.orig).__name__)
    return _json(500, "Internal server error")


async def _pool_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error("database pool/connection problem on %s %s: %s", request.method, request.url.path, type(exc).__name__)
    return _unavailable()


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path, exc_info=exc)
    return _json(500, "Internal server error")


def install(app: FastAPI) -> None:
    app.add_exception_handler(sa_exc.DBAPIError, _dbapi_error)
    app.add_exception_handler(sa_exc.TimeoutError, _pool_error)
    app.add_exception_handler(sa_exc.DisconnectionError, _pool_error)
    app.add_exception_handler(Exception, _unhandled)
