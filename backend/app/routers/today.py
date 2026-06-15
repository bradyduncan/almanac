"""Today's scored item queue (lessons + activities)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import service
from app.db import get_session
from app.scheduler import LESSON
from app.schemas import DrillOut, LessonFactOut, QueueItemOut, TodayOut

router = APIRouter(tags=["today"])


@router.get("/today", response_model=TodayOut)
def get_today(session: Session = Depends(get_session)) -> TodayOut:
    result = service.build_today(session, service.CURRENT_USER_ID, today=date.today())
    titles = result["domain_title_by_id"]
    drill_by_id = result["drill_by_id"]
    lesson_by_id = result["lesson_by_id"]

    items: list[QueueItemOut] = []
    for sd in result["scored"]:
        common = {
            "kind": sd.kind,
            "domain_title": titles[sd.domain_id],
            "score": sd.score,
            "forced": sd.forced,
            "factors": sd.factors,
        }
        if sd.kind == LESSON:
            items.append(
                QueueItemOut(
                    **common, lesson=LessonFactOut.model_validate(lesson_by_id[sd.item_id])
                )
            )
        else:
            items.append(
                QueueItemOut(**common, drill=DrillOut.model_validate(drill_by_id[sd.item_id]))
            )

    return TodayOut(
        day=date.today(),
        goal=result["goal"],
        queued=len(items),
        items=items,
    )
