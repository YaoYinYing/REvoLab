"""Ephemeral last-mile credential materialization value object.

`CredentialLease` is the neutral value object the Provider invocation layer
builds for exactly one capability invocation. It is the only surface through
which Driver transport code may reach secret material:

    credentials.get(kind)

It is intentionally in its own leaf module (like `MutationGrant`) so Driver and
Provider code can accept it without importing Identity or the Secret store.

Security contract (ADR-0012 / TODO.md section 3):

- never persisted;
- never serialized / pickled (`__getstate__` raises);
- never returned through a normal API response;
- never logged (the repr carries only the number of materialized kinds);
- supports the provider's plural `required_credential_kinds`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import NoReturn


class CredentialLeaseError(Exception):
    """Base error for credential-lease materialization failures."""


class CredentialMissingError(CredentialLeaseError, KeyError):
    """A requested credential kind is not present in the lease."""


@dataclass(frozen=True)
class CredentialLease:
    """An invocation-scoped, ephemeral mapping of credential kind -> material."""

    _material: Mapping[str, str] = field(repr=False, compare=False)

    def get(self, kind: str) -> str:
        try:
            return self._material[kind]
        except KeyError as exc:
            raise CredentialMissingError(
                f"credential kind not present in lease: {kind!r}"
            ) from exc

    def __repr__(self) -> str:
        # Never expose secret material or even the kind vocabulary in repr/logs.
        return f"<CredentialLease: {len(self._material)} kind(s)>"

    __str__ = __repr__

    def __getstate__(self) -> NoReturn:
        raise TypeError("CredentialLease must not be pickled or serialized")
