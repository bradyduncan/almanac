# CLAUDE.md — Almanac

This file is read by Claude Code at session start. Keep it current. If a decision here is wrong or stale, change it here first, then in code.

## What this is

A single-user dashboard for tracking progress through a fixed set of life-skill domains (social calibration, assertiveness, conflict/negotiation, hosting/cooking, long-view decision-making, etiquette, health, home/car/travel competence, style/grooming, first aid). Each domain has a few "what to know" facts and a set of short drills. The app surfaces a daily drill queue, logs completions, and tracks per-domain streaks and coverage.

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

Seven tables, split into a shared **catalog** (same for every user) and per-user **progress**. This split is what makes multi-user additive: the catalog stays global, progress keys on `user_id`. Keep it this small unless a feature needs more.

Catalog (global):
- **domain** — `id`, `slug`, `title`, `default_priority` (int; lower = higher; seed value only, user weights override), `core_idea` (text).
- **lesson_fact** — `id`, `domain_id`, `body` (one "what to know" item), `order`.
- **drill** — `id`, `domain_id`, `title`, `kind` (enum: `script` | `reflection` | `rehearsal` | `checklist` | `audit` | `record_review`), `est_minutes` (int), `instructions` (text).
- **source_ref** — `id`, `domain_id` (nullable), `url`, `title`, `fetched_at` (nullable), `cached_path` (nullable). Backs the `fetch` utility; not user-facing content.

Progress (per user):
- **user** — `id`, `handle`, `created_at`, `active_days_per_week` (default 7), `daily_minutes` (default 15). Seed exactly one row now (`id=1`). Today, `user_id` is hardcoded to 1 at the API boundary; multi-user later means resolving it from auth instead. Do not thread auth concerns deeper than that boundary.
- **user_domain_pref** — `id`, `user_id`, `domain_id`, `weight` (float, the user's steering input), `active` (bool). This is how the user self-guides priorities. Absent row = use `default_priority`.
- **drill_log** — `id`, `user_id`, `drill_id`, `logged_at` (datetime), `outcome` (enum: `done` | `skipped` | `snoozed`), `difficulty` (nullable int 1–3, set on `done`: 1 easy, 3 hard), `note` (text, nullable). The event log everything derives from; the selection policy reads `outcome` and `difficulty` directly.

Streaks, coverage, and the daily queue are computed per user from these rows, not stored.

## Scheduler / selection policy

Self-guided: today's queue is scored and ranked per user, not pulled from a fixed rotation. The fixed loop is only a cold-start default and a config value (`active_days_per_week`, set to 7).

- **Daily queue**: score every eligible drill, then pack greedily by score — add highest-scored drills until the next would push the summed `est_minutes` over `daily_minutes`, then stop. Always return at least one drill even if a single drill exceeds the budget (never an empty day). Score is a weighted sum of transparent factors:
  - *neglect* — days since the user last logged anything in that domain (higher = more due),
  - *priority* — the user's per-domain weight (from `user_domain_pref`); this is the steering input,
  - *difficulty* — user rates each `done` drill 1–3 (1 easy, 3 hard); hard drills resurface sooner, easy ones decay,
  - *spacing* — suppress a drill done too recently (no same-drill repeat inside a cooldown window),
  - *novelty* — small bonus for never-attempted drills.
  Keep the weights as named constants at the top of `scheduler.py` so they're easy to tune. Do not hide them in the formula.
- **Minimum-breadth floor**: any domain untouched for longer than a threshold is force-included regardless of its priority weight. This prevents the user steering into only comfortable domains and starving weak areas — the failure mode self-direction invites. Non-negotiable; keep it.
- **Cold start**: before there's enough log history to score against, fall back to the source doc's rotation order to seed variety.
- **Streak (per domain)**: consecutive active days with ≥1 completed drill in that domain. "Active day" respects `active_days_per_week` so a deliberate off day doesn't break it.
- **Coverage (per domain)**: distinct `lesson_fact` reviewed and distinct `drill` completed, over total. Resurface unreviewed facts before completed ones.
- Keep all of this in `scheduler.py` as pure functions taking (the user's log + their prefs + config + today's date) and returning the ranked queue. No DB or `datetime.now()` inside; pass them in. This is the most-tested module in the repo.

## Content authoring format

One markdown file per domain in `content/`, YAML frontmatter + body. `seed.py` parses these into the DB; editing markdown and re-seeding is the content workflow.

```markdown
---
slug: social-calibration
title: Social calibration & nonverbal game
default_priority: 1
core_idea: You're training how you come across on default settings.
facts:
  - First impressions form within seconds, driven by posture, eye contact, expression, grooming, dress.
  - Nonverbal channels often outweigh literal words for warmth and competence judgments.
drills:
  - title: Mirror check
    kind: rehearsal
    est_minutes: 2
    instructions: Relaxed upright posture, neutral-friendly face, steady eye contact for two minutes.
  - title: First-10-seconds drill
    kind: record_review
    est_minutes: 10
    instructions: Record yourself entering, greeting, sitting; review for fidgeting, slouch, vocal energy.
---

Optional prose notes for this domain go here. Not required.
```

`seed.py` must be idempotent: re-running updates existing rows (match on `slug`/`title`) rather than duplicating, and never deletes `drill_log` history.

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
- Scheduler/streak logic stays pure and unit-tested; no DB or `datetime.now()` inside — pass the date in.
- Pydantic schemas separate from SQLAlchemy models; do not return ORM objects from routes.
- No business logic in routes; routes call functions in `scheduler.py` / a thin service layer.
- Errors: raise typed exceptions, map to HTTP in one place.
- UI: routes return rendered Jinja templates (full page) or HTML fragments (HTMX swaps). Keep templates logic-light; the server owns truth. No client-side state store.

## How Claude Code should work in this repo

- **Plan before editing.** For anything beyond a one-file change, state the plan and which files you'll touch, then proceed.
- **Small commits**, one concern each. Conventional messages.
- **Tests with logic.** New scheduler/streak behavior ships with a `pytest` case. Run `pytest` and `ruff` before claiming done.
- **No silent dependencies.** Adding a package requires a one-line reason. Prefer the standard library and what's already here.
- **Don't expand the data model or add a service** without saying why it's needed and getting a yes.
- **Respect the scraping decision.** Do not build a general crawler or wire fetched pages into user-facing content.
- **Idempotent seeds and reversible migrations** — never write a migration or seed that can drop `drill_log`.
- If a requirement is ambiguous, ask one specific question rather than guessing across several files.

## Build order (milestones)

1. Schema + Alembic migration + `seed.py`, with `content/` authored from `docs/source.md`. Verify a clean DB seeds correctly and re-seeds without duplicating.
2. API: domains/lessons/drills read endpoints, `POST /logs` to record a completion, `GET /today` for the queue. Scheduler logic unit-tested.
3. Dashboard UI: today's queue with check-off, per-domain streak and coverage, a domain detail view.
4. (Optional) `fetch` utility + `source_ref` management, used only to assist authoring.

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
- `active_days_per_week` default = 7; `daily_minutes` default = 15.
- Scheduler is a self-guided scoring policy with a minimum-breadth floor, not a fixed rotation. Queue packs greedily by score against `daily_minutes`, never empty.
- Difficulty scale is 1–3 (1 easy, 3 hard), set on `done`.
- Build order: ship the tracker (milestones 1–3) before the Phase 2 discovery layer.

## Open questions

1. Phase 2 discovery layer — document its section in this file now, or after milestones 1–3 ship? (Recommendation: after.)
