"""Typed domain exceptions, mapped to HTTP in exactly one place (main.py).

Routes and the service layer raise these; they never construct HTTPException directly.
"""

from __future__ import annotations


class AppError(Exception):
    """Base for all application errors."""


class NotFoundError(AppError):
    """A requested entity does not exist."""

    def __init__(self, entity: str, key: object) -> None:
        self.entity = entity
        self.key = key
        super().__init__(f"{entity} not found: {key!r}")
