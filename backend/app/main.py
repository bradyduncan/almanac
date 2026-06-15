"""FastAPI app + router wiring + the single typed-exception -> HTTP mapping.

The Jinja/HTMX dashboard (Milestone 3) is served by this same app later.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.exceptions import AppError, NotFoundError
from app.routers import catalog, logs, today

app = FastAPI(title="Almanac", version="0.1.0")

app.include_router(catalog.router)
app.include_router(logs.router)
app.include_router(today.router)


@app.exception_handler(NotFoundError)
def _handle_not_found(_: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(AppError)
def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
