# CLAUDE.md — Almanac

This file is read by Claude Code at session start. Keep it current. If a decision here is wrong or stale, change it here first, then in code.

## What this is

A single-user, **gamified learning app** for a fixed set of life-skill domains (social calibration, assertiveness, conflict/negotiation, hosting/cooking, long-view decision-making, etiquette, health, home/car/travel competence, style/grooming, first aid). The user **browses sections (= domains) and picks one by interest** — nothing is assigned. Each section progresses through three gated levels (beginner → intermediate → advanced); each level holds ordered **lessons** (teaching prose) and **activities** (confirm-you-did-it tasks and auto-graded multiple-choice quizzes). Finishing a level unlocks the next. The app tracks per-section/level completion, a day streak, and XP, in a friendly Duolingo-style UI.

There is no assigned daily queue or scheduler — learning is self-directed, browse-and-pick, with difficulty progression.

The 10 domains and their drills come from a source document (`docs/source.md`); seed data is derived from it.

Scope now: one local user, runs locally. Designed so multi-user is an additive change rather than a rewrite — see Path to multi-user. Do not build auth, accounts, or Postgres yet. The only forward-compatibility work done now is the catalog/progress split and a `user_id` seam; both are cheap and are also correct single-user design.

## Core design decision: lessons are authored, not scraped

Lesson content lives as markdown in `content/` and is the source of truth, committed to git. There is **no content scraper**.

Reason: arbitrary scraping yields inconsistent quality, creates copyright/ToS exposure, and is expensive to maintain, while the subject matter is stable and small enough to write once and edit.

A `fetch` utility may exist to pull and cache *reference pages you explicitly register* (with URL + attribution, respecting `robots.txt`). Its only job is to help draft or revise lesson markdown. Its output never renders to end users as primary content. Do not turn it into a general crawler.

## Stack

Decided:
- **Python 3.11+** backend.
- **FastAPI** for the API.
- **SQLite** via SQLAlchemy 2.0 (single user, local; no server to run). Migrations with Alembic.
- **Pydantic v2** for schemas.
- **Jinja2 templates + HTMX** for the UI, server-rendered by FastAPI. Python + HTML, minimal JS; HTMX handles check-offs and partial updates over HTML fragments.
- **uv** for Python env/deps.
- **pytest** for backend tests; **ruff** for lint+format.

Alternatives, only if a trigger applies — do not switch silently:
- Multi-user / hosting is the trigger to move SQLite to Postgres (the SQLAlchemy layer makes this small). Single-machine, single-user stays on SQLite.
- React + Vite + TS only if you later want heavy client-side interactivity the dashboard doesn't currently need. The API is already JSON-capable, so adding a React frontend later is additive. Do not start there.

## Repo layout

```
almanac/
  CLAUDE.md
  docs/
    source.md            # the original domain/drill source doc
  content/
    01-social.md         # one file per domain, authored lessons (see format below)
    ...
  backend/
    pyproject.toml
    app/
      main.py            # FastAPI app + router wiring
      db.py              # engine, session
      models.py          # SQLAlchemy models
      schemas.py         # Pydantic
      seed.py            # parse content/ -> DB
      scheduler.py       # daily queue + streak logic
      fetch.py           # optional reference fetcher (see design decision)
      routers/
      templates/         # Jinja2 + HTMX
      static/
    migrations/          # alembic
    tests/
```

## Data model

Eight tables, split into a shared **catalog** (same for every user) and per-user **progress**. This split is what makes multi-user additive: the catalog stays global, progress keys on `user_id`. Keep it this small unless a feature needs more.

A domain (= "section") holds ordered **lessons** (teaching units) and ordered **activities** (drills), each tagged with a **level** (beginner / intermediate / advanced). Activities are either "confirm-you-did-it" tasks or auto-graded multiple-choice **quizzes**.

Catalog (global):
- **domain** — `id`, `slug`, `title`, `default_priority` (int; lower = higher; used only for display order on the home page), `core_idea` (text).
- **lesson_fact** — `id`, `domain_id`, `level` (enum: `beginner` | `intermediate` | `advanced`), `title`, `body` (teaching prose), `order` (within `(domain, level)`). Table name kept; represents a "lesson".
- **drill** — `id`, `domain_id`, `level` (same enum), `title`, `kind` (enum: `script` | `reflection` | `rehearsal` | `checklist` | `audit` | `record_review` | `confirm` | `quiz`), `est_minutes` (int), `instructions` (text), `order` (within `(domain, level)`). Quiz-only, nullable: `prompt` (text), `choices` (JSON list[str]), `answer_index` (int). The answer is never sent with the **question** (`DrillOut` omits `answer_index`); grading is server-side and the graded result may reveal the answer as feedback.
- **source_ref** — `id`, `domain_id` (nullable), `url`, `title`, `fetched_at` (nullable), `cached_path` (nullable). Backs the `fetch` utility; not user-facing content.

Progress (per user):
- **user** — `id`, `handle`, `created_at`, `active_days_per_week` (default 7), `daily_minutes` / `daily_items` (legacy columns from the old daily-queue model; currently unused — safe to drop in a later migration). Seed exactly one row now (`id=1`). `user_id` is hardcoded to 1 at the API boundary; multi-user later means resolving it from auth instead.
- **user_domain_pref** — `id`, `user_id`, `domain_id`, `weight` (float), `active` (bool). Legacy steering input from the scored-queue model; not currently read.
- **drill_log** — `id`, `user_id`, `drill_id`, `logged_at` (datetime), `outcome` (enum: `done` | `skipped` | `snoozed`), `difficulty` (nullable int 1–3, set on `done` for confirm activities), `correct` (nullable bool, set on quiz answers), `note` (text, nullable). The event log activity progress derives from.
- **fact_review** — `id`, `user_id`, `fact_id`, `reviewed_at` (datetime). Event log of a user marking a lesson "learned". Backs lesson progress.

Progression, gating, XP, and streaks are computed per user from these rows, not stored.

## Progression, gating & XP

Self-directed: the user picks a section, then works a level's items in order. There is **no scored daily queue and no scheduler** (removed in v3).

- **Completion** — a lesson is complete when a `fact_review` exists; a confirm activity when a `done` `drill_log` exists; a **quiz when a `correct` answer has been logged** (retry until correct). A level is complete when all its items are complete.
- **Gating** — beginner is unlocked if it has content; each later level unlocks once the previous level is complete. A level with no content yet is "coming soon" and blocks what follows. Enforced in `service.level_detail` (raises `BadRequestError`); the UI redirects locked/empty levels back to the section.
- **XP** (computed, not stored) — `XP_LESSON`=10, `XP_ACTIVITY`=15, `XP_QUIZ`=20 per completed item; constants in `service.py`.
- **Streak** — consecutive active days with ≥1 completed activity (any section). "Active day" respects `active_days_per_week`. Pure function in `metrics.py` (no DB, no wall-clock — date passed in); `metrics.py` is the most-tested module.
- Progression/gating/XP live in `service.py` (DB-backed); keep the pure streak math in `metrics.py`.

## Content authoring format

One markdown file per domain in `content/`, YAML frontmatter + body. `seed.py` parses these into the DB; editing markdown and re-seeding is the content workflow.

```markdown
---
slug: social-calibration
title: Social calibration & nonverbal game
default_priority: 1
core_idea: You're training how you come across on default settings.
levels:
  beginner:
    lessons:
      - title: First impressions form in seconds
        body: |
          Multi-paragraph teaching prose, rendered as a "Mark learned" card.
    activities:
      - title: Mirror check          # confirm-style activity
        kind: rehearsal
        est_minutes: 2
        instructions: Relaxed upright posture, neutral-friendly face, steady eye contact.
      - title: Quiz — the two judgment axes
        kind: quiz
        est_minutes: 1
        prompt: Most social judgments collapse onto which two axes?
        choices:
          - Wealth and humor
          - Warmth and competence
          - Dominance and age
        answer_index: 1
  intermediate:
    lessons: [ ... ]
    activities: [ ... ]
  advanced:
    lessons: [ ... ]
    activities: [ ... ]
---
```

Frontmatter requires a `levels:` mapping (`beginner` / `intermediate` / `advanced`); each level has ordered `lessons` (`title` + prose `body`) and `activities`. A level may be omitted or empty ("coming soon"). `activities` with `kind: quiz` require `prompt`, `choices` (≥2), and a valid `answer_index`; every other kind requires `instructions`. `seed.py` must be idempotent: re-running updates existing rows (lessons match on `(domain, level, order)`, activities on `(domain, title)`) rather than duplicating, and never deletes `drill_log` / `fact_review` history.

**Content status:** Social calibration and Conflict/negotiation are fully authored across all three levels; the other eight sections currently have beginner-level content only (intermediate/advanced are "coming soon" until written).

## Build / run / test

Fill these in as the project takes shape; treat the list as the command contract.

```
# backend
cd backend && uv sync
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --reload

# tests + lint
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

The UI is served by the same `uvicorn` process (Jinja + HTMX); there is no separate frontend build.

## Coding conventions

- Type hints everywhere; functions that touch the DB take a `Session` argument rather than opening their own.
- Streak logic stays pure and unit-tested in `metrics.py`; no DB or `datetime.now()` inside — pass the date in.
- Pydantic schemas separate from SQLAlchemy models; do not return ORM objects from JSON routes.
- No business logic in routes; routes call the thin `service.py` layer.
- Errors: raise typed exceptions, map to HTTP in one place.
- UI: routes return rendered Jinja templates (full page) or HTML fragments (HTMX swaps). Keep templates logic-light; the server owns truth. No client-side state store.

## How Claude Code should work in this repo

- **Plan before editing.** For anything beyond a one-file change, state the plan and which files you'll touch, then proceed.
- **Small commits**, one concern each. Conventional messages.
- **Tests with logic.** New progression/gating/streak behavior ships with a `pytest` case. Run `pytest` and `ruff` before claiming done.
- **No silent dependencies.** Adding a package requires a one-line reason. Prefer the standard library and what's already here.
- **Don't expand the data model or add a service** without saying why it's needed and getting a yes.
- **Respect the scraping decision.** Do not build a general crawler or wire fetched pages into user-facing content.
- **Idempotent seeds and reversible migrations** — never write a migration or seed that can drop `drill_log`.
- If a requirement is ambiguous, ask one specific question rather than guessing across several files.

## Build status

Shipped: schema + idempotent seed; catalog/logs/quiz API; gamified browse-and-pick UI (home → section → level) with gated levels, XP, day streak, lessons + auto-graded quizzes. The earlier scored-daily-queue scheduler was built then **removed in v3** in favor of self-directed progression.

Remaining / optional:
- Author intermediate + advanced content for the eight beginner-only sections.
- (Optional) `fetch` utility + `source_ref` management, used only to assist authoring.
- Drop the unused legacy columns (`user.daily_minutes`/`daily_items`, `user_domain_pref`) in a cleanup migration.

## Path to multi-user

Build none of this now. It is recorded so today's choices don't block it.

Already in place as seams (do not skip these):
- Catalog/progress split in the schema.
- A `user` table; `user_id` resolved at one API boundary, hardcoded to 1 for now.
- Scheduler logic is per-user and pure.

Deferred until there's a real second user, roughly in order:
1. Auth: pick one provider (e.g. an OAuth library or a hosted identity service); resolve `user_id` from the session at the boundary that currently hardcodes 1. Backend logic below that line is unchanged.
2. Postgres: swap the engine URL, run migrations, move off the local SQLite file.
3. Per-user UI state and a login flow on the frontend.
4. Only if users should author or customize content: per-user overrides on top of the shared catalog. Until then the catalog stays global and authored-in-git.

Non-goal even at multi-user: a content scraper. The authored-markdown catalog is the model whether there is one user or many.

## Non-goals

Out of scope, build only on an explicit decision recorded here:
- A content scraper as a feature (also excluded at multi-user, above).
- Mobile/native app.
- Notifications/email.
- Social features (sharing, leaderboards, following).

Deferred, not forbidden — planned for multi-user, see Path to multi-user: auth, accounts, hosting, Postgres.

## Decisions log

- Stack is Python end-to-end: FastAPI + Jinja + HTMX, SQLite, uv. No React, no separate frontend build.
- **v3 model: browse-and-pick, not assigned.** The user chooses a section and progresses through gated levels (beginner → intermediate → advanced). The earlier scored daily queue + `scheduler.py` were removed; `user.daily_*` and `user_domain_pref` are now-unused legacy columns.
- Content is structured per domain as **levels**, each with **lessons** (teaching prose, marked "learned") then **activities** (confirm-you-did-it tasks or auto-graded multiple-choice **quizzes**). Quiz answer is server-side; the question payload omits it; the result reveals it as feedback.
- **Gating**: a level unlocks once the previous is complete (all items done; quiz needs a correct answer). Empty levels are "coming soon".
- **Gamified**: day streak + XP (lesson 10 / activity 15 / quiz 20, computed) + per-level progress, Duolingo-style UI.
- Difficulty scale 1–3 (1 easy, 3 hard) recorded on confirm-activity `done`; quizzes record `correct` instead.
- Teachings stay high-level and accessible but useful; sections span multiple levels so they take real time.

## Open questions

1. Should completed sections suggest a "next section" or stay purely browse-driven? (Currently: pure browse.)
2. Add explicit gamification beyond XP/streak (badges, daily XP goal) — worth it for a single user?
