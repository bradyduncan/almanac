"""Server-rendered dashboard (Jinja + HTMX).

Templates are logic-light; the server owns truth. POST endpoints return HTML fragments
that HTMX swaps in place. No client-side state store.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import service
from app.db import get_session
from app.models import Drill, DrillKind, LessonFact
from app.scheduler import LESSON

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

router = APIRouter(include_in_schema=False)


def _choices(drill: Drill) -> list[str] | None:
    return json.loads(drill.choices) if drill.choices else None


def _drill_view(drill: Drill, done: bool = False) -> dict:
    return {
        "drill": drill,
        "is_quiz": drill.kind == DrillKind.quiz,
        "choices": _choices(drill),
        "done": done,
    }


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    today = date.today()
    result = service.build_today(session, service.CURRENT_USER_ID, today)
    titles = result["domain_title_by_id"]
    drill_by_id = result["drill_by_id"]
    lesson_by_id = result["lesson_by_id"]

    items = []
    for sd in result["scored"]:
        view = {"kind": sd.kind, "domain_title": titles[sd.domain_id], "forced": sd.forced}
        if sd.kind == LESSON:
            view["lesson"] = lesson_by_id[sd.item_id]
        else:
            view.update(_drill_view(drill_by_id[sd.item_id]))
        items.append(view)

    progress = service.domain_progress(session, service.CURRENT_USER_ID, today)
    return TEMPLATES.TemplateResponse(
        request,
        "dashboard.html",
        {
            "items": items,
            "goal": result["goal"],
            "queued": len(items),
            "progress": progress,
        },
    )


@router.get("/d/{slug}", response_class=HTMLResponse)
def domain_detail(
    slug: str, request: Request, session: Session = Depends(get_session)
) -> HTMLResponse:
    detail = service.domain_detail_progress(session, service.CURRENT_USER_ID, slug)
    drills = [_drill_view(d["drill"], d["done"]) for d in detail["drills"]]
    return TEMPLATES.TemplateResponse(
        request,
        "domain_detail.html",
        {"domain": detail["domain"], "facts": detail["facts"], "drills": drills},
    )


@router.post("/ui/drill-log", response_class=HTMLResponse)
def ui_drill_log(
    request: Request,
    drill_id: int = Form(...),
    outcome: str = Form(...),
    difficulty: int | None = Form(None),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    from app.models import LogOutcome
    from app.schemas import LogCreate

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
