"""Read-only catalog endpoints: domains, lessons, drills."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import service
from app.db import get_session
from app.schemas import DomainDetailOut, DomainOut, DrillOut, LessonFactOut

router = APIRouter(tags=["catalog"])


@router.get("/domains", response_model=list[DomainOut])
def get_domains(session: Session = Depends(get_session)) -> list[DomainOut]:
    return [DomainOut.model_validate(d) for d in service.list_domains(session)]


@router.get("/domains/{slug}", response_model=DomainDetailOut)
def get_domain(slug: str, session: Session = Depends(get_session)) -> DomainDetailOut:
    return DomainDetailOut.model_validate(service.get_domain_detail(session, slug))


@router.get("/domains/{slug}/facts", response_model=list[LessonFactOut])
def get_domain_facts(slug: str, session: Session = Depends(get_session)) -> list[LessonFactOut]:
    return [LessonFactOut.model_validate(f) for f in service.list_facts(session, slug)]


@router.get("/drills", response_model=list[DrillOut])
def get_drills(
    domain: str | None = None, session: Session = Depends(get_session)
) -> list[DrillOut]:
    return [DrillOut.model_validate(d) for d in service.list_drills(session, domain)]
