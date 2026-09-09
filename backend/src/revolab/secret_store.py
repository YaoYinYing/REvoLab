"""Credential / Secret store boundary.

`SecretStore` owns secret material. Core and domain persistence do not: the
database stores only an opaque `secret_ref`, never the material itself.

This module is a neutral leaf (like `revolab.content_store`): both the
Identity/Collaboration domain (which owns the non-secret binding) and the
Provider/Capability domain (which materializes a `CredentialLease`) may import
it without creating a dependency cycle.

No home-grown cryptography: a production deployment owns secret storage/encryption
in a mature backend. The in-memory adapter here exists only so Phase 3 is
executable and is explicitly named NON-PRODUCTION.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from functools import lru_cache
from typing import NewType, Protocol

from revolab.config import get_settings
from revolab.credentials import CredentialLease

# Opaque, non-reversible handle. The handle is random and never derived from the
# material it locates (a content-derived handle would be an equality oracle).
SecretRef = NewType("SecretRef", str)


class SecretStoreError(Exception):
    """Base error for the Secret store boundary."""


class SecretMissingError(SecretStoreError):
    """The referenced secret material does not exist (or was revoked)."""


class SecretStore(Protocol):
    """The smallest durable secret-material boundary.

    `put` materializes a secret and returns an opaque locator. `exists` probes
    presence WITHOUT materializing — the presence-test primitive for any caller
    that must never retrieve material. `lease` materializes exactly the
    requested kinds for one invocation and is all-or-nothing.
    """

    def put(self, value: str) -> SecretRef: ...

    def get(self, ref: SecretRef) -> str: ...

    def exists(self, ref: SecretRef) -> bool: ...

    def delete(self, ref: SecretRef) -> None: ...

    def lease(self, requested: Mapping[str, SecretRef]) -> CredentialLease: ...


class InMemorySecretStore:
    """NON-PRODUCTION, test/development-only SecretStore adapter.

    Secrets live only in process memory. They are NOT durable, NOT encrypted at
    rest, and NOT a production-grade secret backend. This adapter refuses to be
    constructed when the application environment is `production`.
    """

    def __init__(self) -> None:
        if get_settings().environment == "production":
            raise SecretStoreError(
                "InMemorySecretStore is a non-production adapter and must not be "
                "used when REVOLAB_ENVIRONMENT=production"
            )
        self._material: dict[str, str] = {}

    def put(self, value: str) -> SecretRef:
        ref = SecretRef(secrets.token_urlsafe(32))
        self._material[ref] = value
        return ref

    def get(self, ref: SecretRef) -> str:
        try:
            return self._material[ref]
        except KeyError as exc:
            raise SecretMissingError("secret material is not present for the given reference") from exc

    def exists(self, ref: SecretRef) -> bool:
        return ref in self._material

    def delete(self, ref: SecretRef) -> None:
        self._material.pop(ref, None)

    def lease(self, requested: Mapping[str, SecretRef]) -> CredentialLease:
        material: dict[str, str] = {}
        for kind, ref in requested.items():
            if ref not in self._material:
                raise SecretMissingError(
                    f"secret material is not present for credential kind: {kind!r}"
                )
            material[kind] = self._material[ref]
        return CredentialLease(material)


@lru_cache
def default_secret_store() -> SecretStore:
    """Application-scoped Secret store singleton (non-production adapter)."""
    return InMemorySecretStore()
