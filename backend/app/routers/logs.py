"""Record drill completions."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app import service
from app.db import get_session
from app.schemas import (
    FactReviewCreate,
    FactReviewOut,
    LogCreate,
    LogOut,
    QuizAnswer,
    QuizResult,
)

router = APIRouter(tags=["logs"])


@router.post("/logs", response_model=LogOut, status_code=status.HTTP_201_CREATED)
def create_log(data: LogCreate, session: Session = Depends(get_session)) -> LogOut:
    # datetime.now() lives at the API boundary, never inside the scheduler.
    log = service.create_log(session, service.CURRENT_USER_ID, data, now=datetime.now())
    return LogOut.model_validate(log)


@router.post("/fact-reviews", response_model=FactReviewOut, status_code=status.HTTP_201_CREATED)
def create_fact_review(
    data: FactReviewCreate, session: Session = Depends(get_session)
) -> FactReviewOut:
    review = service.create_fact_review(
        session, service.CURRENT_USER_ID, data.fact_id, now=datetime.now()
    )
    return FactReviewOut.model_validate(review)


@router.post("/quiz-answers", response_model=QuizResult, status_code=status.HTTP_201_CREATED)
def submit_quiz_answer(data: QuizAnswer, session: Session = Depends(get_session)) -> QuizResult:
    drill, correct = service.grade_quiz(
        session, service.CURRENT_USER_ID, data.drill_id, data.choice_index, now=datetime.now()
    )
    return QuizResult(
        drill_id=drill.id,
        correct=correct,
        chosen_index=data.choice_index,
        answer_index=drill.answer_index,
    )
