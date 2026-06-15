"""Parse content/*.md into the catalog and ensure the single seed user exists.

Idempotent by contract (CLAUDE.md): re-running updates existing rows rather than
duplicating, and never deletes drill_log / fact_review history.

Each domain file has YAML frontmatter with a `levels:` mapping (beginner / intermediate /
advanced). Each level holds ordered `lessons` (title + prose body) and ordered
`activities` (kind == "quiz" carries prompt/choices/answer_index; any other kind is a
"confirm you did it" activity with instructions).

Matching keys:
  - domain      -> slug
  - lesson_fact -> (domain_id, level, order)
  - drill       -> (domain_id, title)

Run: uv run python -m app.seed
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import LEVEL_ORDER, Domain, Drill, DrillKind, LessonFact, Level, User

CONTENT_DIR = Path(__file__).resolve().parents[2] / "content"

SEED_USER_ID = 1
SEED_USER_HANDLE = "me"

QUIZ_KIND = DrillKind.quiz.value
LEVEL_NAMES = [lvl.value for lvl in LEVEL_ORDER]


@dataclass
class LessonSpec:
    title: str
    body: str
    level: str
    order: int


@dataclass
class ActivitySpec:
    title: str
    kind: str
    est_minutes: int
    level: str
    order: int
    instructions: str = ""
    prompt: str | None = None
    choices: list[str] | None = None
    answer_index: int | None = None


@dataclass
class DomainSpec:
    slug: str
    title: str
    default_priority: int
    core_idea: str
    lessons: list[LessonSpec] = field(default_factory=list)
    activities: list[ActivitySpec] = field(default_factory=list)


def _parse_frontmatter(text: str, source: Path) -> dict:
    if not text.lstrip().startswith("---"):
        raise ValueError(f"{source.name}: missing opening '---' frontmatter delimiter")
    body = text.split("---", 2)
    if len(body) < 3:
        raise ValueError(f"{source.name}: missing closing '---' frontmatter delimiter")
    data = yaml.safe_load(body[1])
    if not isinstance(data, dict):
        raise ValueError(f"{source.name}: frontmatter did not parse to a mapping")
    return data


def _parse_activity(
    a: dict, source: Path, valid_kinds: set[str], level: str, order: int
) -> ActivitySpec:
    title = a["title"]
    kind = a["kind"]
    if kind not in valid_kinds:
        raise ValueError(f"{source.name}: activity '{title}' has invalid kind '{kind}'")

    if kind == QUIZ_KIND:
        prompt = a.get("prompt")
        choices = a.get("choices")
        answer_index = a.get("answer_index")
        if not prompt:
            raise ValueError(f"{source.name}: quiz '{title}' missing prompt")
        if not isinstance(choices, list) or len(choices) < 2:
            raise ValueError(f"{source.name}: quiz '{title}' needs >=2 choices")
        if not isinstance(answer_index, int) or not 0 <= answer_index < len(choices):
            raise ValueError(f"{source.name}: quiz '{title}' has invalid answer_index")
        return ActivitySpec(
            title=title,
            kind=kind,
            est_minutes=int(a.get("est_minutes", 1)),
            level=level,
            order=order,
            instructions=a.get("instructions", ""),
            prompt=prompt,
            choices=[str(c) for c in choices],
            answer_index=answer_index,
        )

    if not a.get("instructions"):
        raise ValueError(f"{source.name}: activity '{title}' missing instructions")
    return ActivitySpec(
        title=title,
        kind=kind,
        est_minutes=int(a["est_minutes"]),
        level=level,
        order=order,
        instructions=a["instructions"],
    )


def parse_domain_file(path: Path) -> DomainSpec:
    data = _parse_frontmatter(path.read_text(encoding="utf-8"), path)
    valid_kinds = {k.value for k in DrillKind}

    levels = data.get("levels")
    if not isinstance(levels, dict):
        raise ValueError(f"{path.name}: frontmatter needs a 'levels:' mapping")

    lessons: list[LessonSpec] = []
    activities: list[ActivitySpec] = []
    for level in LEVEL_NAMES:
        block = levels.get(level)
        if not block:
            continue
        for i, lsn in enumerate(block.get("lessons", [])):
            lessons.append(LessonSpec(title=lsn["title"], body=lsn["body"], level=level, order=i))
        for i, a in enumerate(block.get("activities", [])):
            activities.append(_parse_activity(a, path, valid_kinds, level, i))

    return DomainSpec(
        slug=data["slug"],
        title=data["title"],
        default_priority=int(data["default_priority"]),
        core_idea=data["core_idea"],
        lessons=lessons,
        activities=activities,
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
    session.flush()
    return domain


def _upsert_lessons(session: Session, domain: Domain, lessons: list[LessonSpec]) -> None:
    existing = {(str(f.level), f.order): f for f in domain.facts}
    new_keys = {(lsn.level, lsn.order) for lsn in lessons}
    for lsn in lessons:
        fact = existing.get((lsn.level, lsn.order))
        if fact is None:
            session.add(
                LessonFact(
                    domain_id=domain.id,
                    level=Level(lsn.level),
                    order=lsn.order,
                    title=lsn.title,
                    body=lsn.body,
                )
            )
        else:
            fact.title = lsn.title
            fact.body = lsn.body
    # Drop lessons whose (level, order) no longer exists in the source.
    for key, fact in existing.items():
        if key not in new_keys:
            session.delete(fact)


def _upsert_activities(session: Session, domain: Domain, activities: list[ActivitySpec]) -> None:
    existing = {d.title: d for d in domain.drills}
    for spec in activities:
        drill = existing.get(spec.title)
        if drill is None:
            drill = Drill(domain_id=domain.id, title=spec.title)
            session.add(drill)
        drill.level = Level(spec.level)
        drill.order = spec.order
        drill.kind = DrillKind(spec.kind)
        drill.est_minutes = spec.est_minutes
        drill.instructions = spec.instructions
        drill.prompt = spec.prompt
        drill.choices = json.dumps(spec.choices) if spec.choices is not None else None
        drill.answer_index = spec.answer_index
    # Intentionally do NOT delete activities missing from source — drill_log rows
    # reference them. Renaming orphans the old row; handle with a deliberate migration.


def ensure_seed_user(session: Session) -> None:
    user = session.get(User, SEED_USER_ID)
    if user is None:
        session.add(User(id=SEED_USER_ID, handle=SEED_USER_HANDLE))


def seed(session: Session, content_dir: Path = CONTENT_DIR) -> dict[str, int]:
    specs = load_specs(content_dir)
    for spec in specs:
        domain = _upsert_domain(session, spec)
        _upsert_lessons(session, domain, spec.lessons)
        _upsert_activities(session, domain, spec.activities)
    ensure_seed_user(session)
    session.commit()

    return {
        "domains": len(specs),
        "lessons": sum(len(s.lessons) for s in specs),
        "activities": sum(len(s.activities) for s in specs),
        "quizzes": sum(1 for s in specs for a in s.activities if a.kind == QUIZ_KIND),
    }


def main() -> None:
    session = SessionLocal()
    try:
        counts = seed(session)
    finally:
        session.close()
    print(
        f"seeded: {counts['domains']} domains, {counts['lessons']} lessons, "
        f"{counts['activities']} activities ({counts['quizzes']} quizzes)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
