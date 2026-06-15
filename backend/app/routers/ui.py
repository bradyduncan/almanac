"""Server-rendered dashboard (Jinja + HTMX).

Templates are logic-light; the server owns truth. POST endpoints return HTML fragments
that HTMX swaps in place. No client-side state store.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import service
from app.db import get_session
from app.models import Drill, LessonFact, LogOutcome
from app.schemas import LogCreate

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    today = date.today()
    result = service.build_today(session, service.CURRENT_USER_ID, today)
    drill_by_id = result["drill_by_id"]
    titles = result["domain_title_by_id"]

    items = []
    for sd in result["scored"]:
        drill = drill_by_id[sd.drill.id]
        items.append({"drill": drill, "domain_title": titles[drill.domain_id], "forced": sd.forced})

    progress = service.domain_progress(session, service.CURRENT_USER_ID, today)
    return TEMPLATES.TemplateResponse(
        request,
        "dashboard.html",
        {
            "items": items,
            "budget_minutes": result["budget"],
            "queued_minutes": sum(it["drill"].est_minutes for it in items),
            "progress": progress,
        },
    )


@router.get("/d/{slug}", response_class=HTMLResponse)
def domain_detail(
    slug: str, request: Request, session: Session = Depends(get_session)
) -> HTMLResponse:
    detail = service.domain_detail_progress(session, service.CURRENT_USER_ID, slug)
    return TEMPLATES.TemplateResponse(request, "domain_detail.html", detail)


@router.post("/ui/drill-log", response_class=HTMLResponse)
def ui_drill_log(
    request: Request,
    drill_id: int = Form(...),
    outcome: str = Form(...),
    difficulty: int | None = Form(None),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    data = LogCreate(drill_id=drill_id, outcome=LogOutcome(outcome), difficulty=difficulty)
    service.create_log(session, service.CURRENT_USER_ID, data, now=datetime.now())
    drill = session.get(Drill, drill_id)
    return TEMPLATES.TemplateResponse(
        request,
        "_queue_item_logged.html",
        {
            "drill": drill,
            "domain_title": drill.domain.title,
            "outcome": outcome,
            "difficulty": difficulty,
        },
    )


@router.post("/ui/fact-review", response_class=HTMLResponse)
def ui_fact_review(
    request: Request,
    fact_id: int = Form(...),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    service.create_fact_review(session, service.CURRENT_USER_ID, fact_id, now=datetime.now())
    fact = session.get(LessonFact, fact_id)
    return TEMPLATES.TemplateResponse(request, "_fact.html", {"fact": fact, "reviewed": True})
