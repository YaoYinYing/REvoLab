"""Typed domain errors mapped to HTTP statuses at the API boundary.

Service code raises these; it never imports FastAPI, so the domain stays free of
the Presentation web framework (domain DAG honesty).
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all typed domain failures."""

    status_code = 400


class NotFoundError(DomainError):
    status_code = 404


class AuthorizationError(DomainError):
    status_code = 403


class ValidationError(DomainError):
    status_code = 422


class ConflictError(DomainError):
    """Immutability / freeze / uniqueness / lifecycle conflicts."""

    status_code = 409
