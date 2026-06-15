"""FastAPI app + router wiring.

Milestone 1 ships only a health check; domain/lesson/drill routes, POST /logs, and
GET /today land in Milestone 2.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="Almanac", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
