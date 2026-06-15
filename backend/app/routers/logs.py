"""Record drill completions."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app import service
from app.db import get_session
from app.schemas import LogCreate, LogOut

router = APIRouter(tags=["logs"])


@router.post("/logs", response_model=LogOut, status_code=status.HTTP_201_CREATED)
def create_log(data: LogCreate, session: Session = Depends(get_session)) -> LogOut:
    # datetime.now() lives at the API boundary, never inside the scheduler.
    log = service.create_log(session, service.CURRENT_USER_ID, data, now=datetime.now())
    return LogOut.model_validate(log)
