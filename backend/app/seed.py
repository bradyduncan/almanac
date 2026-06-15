"""Parse content/*.md into the catalog and ensure the single seed user exists.

Idempotent by contract (CLAUDE.md): re-running updates existing rows rather than
duplicating, and never deletes drill_log history.

Matching keys:
  - domain      -> slug
  - lesson_fact -> (domain_id, order)
  - drill       -> (domain_id, title)

Run: uv run python -m app.seed
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Domain, Drill, DrillKind, LessonFact, User

# repo_root/content — seed.py lives at repo_root/backend/app/seed.py
CONTENT_DIR = Path(__file__).resolve().parents[2] / "content"

SEED_USER_ID = 1
SEED_USER_HANDLE = "me"


@dataclass
class DrillSpec:
    title: str
    kind: str
    est_minutes: int
    instructions: str


@dataclass
class DomainSpec:
    slug: str
    title: str
    default_priority: int
    core_idea: str
    facts: list[str]
    drills: list[DrillSpec]


def _parse_frontmatter(text: str, source: Path) -> dict:
    """Extract the YAML block delimited by leading/closing '---' lines."""
    if not text.lstrip().startswith("---"):
        raise ValueError(f"{source.name}: missing opening '---' frontmatter delimiter")
    body = text.split("---", 2)
    if len(body) < 3:
        raise ValueError(f"{source.name}: missing closing '---' frontmatter delimiter")
    data = yaml.safe_load(body[1])
    if not isinstance(data, dict):
        raise ValueError(f"{source.name}: frontmatter did not parse to a mapping")
    return data


def parse_domain_file(path: Path) -> DomainSpec:
    data = _parse_frontmatter(path.read_text(encoding="utf-8"), path)

    valid_kinds = {k.value for k in DrillKind}
    drills: list[DrillSpec] = []
    for d in data.get("drills", []):
        kind = d["kind"]
        if kind not in valid_kinds:
            raise ValueError(f"{path.name}: drill '{d.get('title')}' has invalid kind '{kind}'")
        drills.append(
            DrillSpec(
                title=d["title"],
                kind=kind,
                est_minutes=int(d["est_minutes"]),
                instructions=d["instructions"],
            )
        )

    return DomainSpec(
        slug=data["slug"],
        title=data["title"],
        default_priority=int(data["default_priority"]),
        core_idea=data["core_idea"],
        facts=list(data.get("facts", [])),
        drills=drills,
    )


def load_specs(content_dir: Path = CONTENT_DIR) -> list[DomainSpec]:
    files = sorted(content_dir.glob("*.md"))
    if not files:
        raise FileNotFoundError(f"no content/*.md files found in {content_dir}")
    return [parse_domain_file(p) for p in files]


def _upsert_domain(session: Session, spec: DomainSpec) -> Domain:
    domain = session.scalar(select(Domain).where(Domain.slug == spec.slug))
    if domain is None:
        domain = Domain(slug=spec.slug)
        session.add(domain)
    domain.title = spec.title
    domain.default_priority = spec.default_priority
    domain.core_idea = spec.core_idea
    session.flush()  # assign domain.id for child matching
    return domain


def _upsert_facts(session: Session, domain: Domain, facts: list[str]) -> None:
    existing = {f.order: f for f in domain.facts}
    for order, body in enumerate(facts):
        fact = existing.get(order)
        if fact is None:
            session.add(LessonFact(domain_id=domain.id, order=order, body=body))
        else:
            fact.body = body
    # Drop facts whose order no longer exists in the source (content shrank).
    for order, fact in existing.items():
        if order >= len(facts):
            session.delete(fact)


def _upsert_drills(session: Session, domain: Domain, drills: list[DrillSpec]) -> None:
    existing = {d.title: d for d in domain.drills}
    for spec in drills:
        drill = existing.get(spec.title)
        if drill is None:
            drill = Drill(domain_id=domain.id, title=spec.title)
            session.add(drill)
        drill.kind = DrillKind(spec.kind)
        drill.est_minutes = spec.est_minutes
        drill.instructions = spec.instructions
    # Note: we intentionally do NOT delete drills missing from source, because
    # drill_log rows reference them. Renaming a drill orphans the old row; handle
    # that with a deliberate migration, never a silent seed-time delete.


def ensure_seed_user(session: Session) -> None:
    user = session.get(User, SEED_USER_ID)
    if user is None:
        session.add(User(id=SEED_USER_ID, handle=SEED_USER_HANDLE))


def seed(session: Session, content_dir: Path = CONTENT_DIR) -> dict[str, int]:
    specs = load_specs(content_dir)
    for spec in specs:
        domain = _upsert_domain(session, spec)
        _upsert_facts(session, domain, spec.facts)
        _upsert_drills(session, domain, spec.drills)
    ensure_seed_user(session)
    session.commit()

    return {
        "domains": len(specs),
        "facts": sum(len(s.facts) for s in specs),
        "drills": sum(len(s.drills) for s in specs),
    }


def main() -> None:
    session = SessionLocal()
    try:
        counts = seed(session)
    finally:
        session.close()
    print(
        f"seeded: {counts['domains']} domains, {counts['facts']} facts, {counts['drills']} drills",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
