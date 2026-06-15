"""Server-rendered, gamified UI (Jinja + HTMX).

Browse sections on the home page, open a section to see its levels (gated), and work
through a level's lessons and activities. POST endpoints return HTML fragments that HTMX
swaps in place. Templates are logic-light; the server owns truth.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import service
from app.db import get_session
from app.exceptions import BadRequestError
from app.models import Drill, DrillKind, LessonFact, LogOutcome
from app.schemas import LogCreate

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

router = APIRouter(include_in_schema=False)


def _choices(drill: Drill) -> list[str] | None:
    return json.loads(drill.choices) if drill.choices else None


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #


@router.get("/", response_class=HTMLResponse)
def home(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    data = service.sections_overview(session, service.CURRENT_USER_ID, date.today())
    return TEMPLATES.TemplateResponse(request, "home.html", data)


@router.get("/d/{slug}", response_class=HTMLResponse)
def section(slug: str, request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    data = service.section_detail(session, service.CURRENT_USER_ID, slug)
    data.update(service.header_stats(session, service.CURRENT_USER_ID, date.today()))
    return TEMPLATES.TemplateResponse(request, "section.html", data)


@router.get("/d/{slug}/{level}", response_class=HTMLResponse)
def level(slug: str, level: str, request: Request, session: Session = Depends(get_session)):
    try:
        data = service.level_detail(session, service.CURRENT_USER_ID, slug, level)
    except BadRequestError:
        # Locked or empty level — send the user back to the section overview.
        return RedirectResponse(f"/d/{slug}", status_code=303)
    data["drills"] = [
        {**dv, "is_quiz": dv["drill"].kind == DrillKind.quiz, "choices": _choices(dv["drill"])}
        for dv in data["drills"]
    ]
    data.update(service.header_stats(session, service.CURRENT_USER_ID, date.today()))
    return TEMPLATES.TemplateResponse(request, "level.html", data)


# --------------------------------------------------------------------------- #
# HTMX action fragments
# --------------------------------------------------------------------------- #


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


@router.post("/ui/quiz-answer", response_class=HTMLResponse)
def ui_quiz_answer(
    request: Request,
    drill_id: int = Form(...),
    choice_index: int = Form(...),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    drill, correct = service.grade_quiz(
        session, service.CURRENT_USER_ID, drill_id, choice_index, now=datetime.now()
    )
    return TEMPLATES.TemplateResponse(
        request,
        "_quiz_result.html",
        {
            "drill": drill,
            "domain_title": drill.domain.title,
            "correct": correct,
            "chosen_index": choice_index,
            "choices": _choices(drill),
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
