"""Today's scored drill queue."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import service
from app.db import get_session
from app.schemas import DrillOut, QueueItemOut, TodayOut

router = APIRouter(tags=["today"])


@router.get("/today", response_model=TodayOut)
def get_today(session: Session = Depends(get_session)) -> TodayOut:
    result = service.build_today(session, service.CURRENT_USER_ID, today=date.today())
    drill_by_id = result["drill_by_id"]
    domain_title_by_id = result["domain_title_by_id"]

    items: list[QueueItemOut] = []
    for sd in result["scored"]:
        drill = drill_by_id[sd.drill.id]
        items.append(
            QueueItemOut(
                drill=DrillOut.model_validate(drill),
                domain_title=domain_title_by_id[drill.domain_id],
                score=sd.score,
                forced=sd.forced,
                factors=sd.factors,
            )
        )

    return TodayOut(
        day=date.today(),
        budget_minutes=result["budget"],
        queued_minutes=sum(it.drill.est_minutes for it in items),
        items=items,
    )
