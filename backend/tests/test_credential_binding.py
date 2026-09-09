"""Phase-3 credential binding + Secret boundary tests (services-level)."""

import pickle

import pytest

from revolab import services
from revolab.credentials import CredentialLease, CredentialMissingError
from revolab.domain.errors import ConflictError, NotFoundError
from revolab.domain.identity import has_credential
from revolab.domain.provider import build_credential_lease
from revolab.models import ExternalProviderCredentialBinding
from revolab.secret_store import SecretMissingError

SENTINEL = "REVOLAB_SENTINEL_0f9e2a7c4b6d8e1f"


def _actor(session):
    return services.create_actor(session)


def test_create_binding_persists_only_opaque_secret_ref(session, secret_store):
    actor = _actor(session)
    binding = services.provision_credential(
        session, secret_store, actor, "fakeprov", "api_key", SENTINEL
    )
    row = session.get(ExternalProviderCredentialBinding, binding.id)
    assert row is not None
    # The durable row carries only the opaque locator, never the material.
    assert row.secret_ref != SENTINEL
    assert SENTINEL not in row.secret_ref
    assert SENTINEL not in repr(row).replace("ExternalProviderCredentialBinding", "")


def test_duplicate_active_binding_same_actor_provider_kind_rejected(session, secret_store):
    actor = _actor(session)
    services.provision_credential(session, secret_store, actor, "fakeprov", "api_key", SENTINEL)
    with pytest.raises(ConflictError):
        services.provision_credential(session, secret_store, actor, "fakeprov", "api_key", "other")


def test_same_actor_may_hold_multiple_providers_and_kinds(session, secret_store):
    actor = _actor(session)
    services.provision_credential(session, secret_store, actor, "prov_a", "api_key", SENTINEL)
    services.provision_credential(session, secret_store, actor, "prov_a", "org_token", SENTINEL)
    services.provision_credential(session, secret_store, actor, "prov_b", "api_key", SENTINEL)
    assert has_credential(session, actor, "prov_a", "api_key")
    assert has_credential(session, actor, "prov_a", "org_token")
    assert has_credential(session, actor, "prov_b", "api_key")


def test_replace_binding_rotates_secret_ref_and_deletes_old_material(session, secret_store):
    actor = _actor(session)
    bindings = services.provision_credential(
        session, secret_store, actor, "fakeprov", "api_key", SENTINEL
    )
    old_ref = bindings.secret_ref

    services.rotate_credential(session, secret_store, actor, "fakeprov", "api_key", "new-secret")

    rotated = session.get(ExternalProviderCredentialBinding, bindings.id)
    assert rotated.secret_ref != old_ref
    assert secret_store.exists(old_ref) is False
    assert secret_store.get(rotated.secret_ref) == "new-secret"


def test_revoke_binding_removes_row_and_secret_material(session, secret_store):
    actor = _actor(session)
    binding = services.provision_credential(session, secret_store, actor, "fakeprov", "api_key", SENTINEL)
    ref = binding.secret_ref

    services.revoke_credential(session, secret_store, actor, "fakeprov", "api_key")

    assert session.get(ExternalProviderCredentialBinding, binding.id) is None
    assert has_credential(session, actor, "fakeprov", "api_key") is False
    assert secret_store.exists(ref) is False


def test_missing_actor_fails(session, secret_store):
    from uuid import UUID

    missing = UUID("00000000-0000-4000-8000-000000000000")
    with pytest.raises(NotFoundError):
        services.provision_credential(session, secret_store, missing, "fakeprov", "api_key", SENTINEL)


def test_missing_revoke_or_rotate_fails(session, secret_store):
    actor = _actor(session)
    with pytest.raises(NotFoundError):
        services.rotate_credential(session, secret_store, actor, "fakeprov", "api_key", "x")
    with pytest.raises(NotFoundError):
        services.revoke_credential(session, secret_store, actor, "fakeprov", "api_key")


def test_actor_cannot_inspect_other_actor_binding(session, secret_store):
    alice = _actor(session)
    bob = _actor(session)
    services.provision_credential(session, secret_store, alice, "fakeprov", "api_key", SENTINEL)

    # Bob has no visibility into Alice's bindings through identity queries or
    # service revocation; a missing reference reads as "not found", not Alice's.
    assert has_credential(session, bob, "fakeprov", "api_key") is False
    with pytest.raises(NotFoundError):
        services.revoke_credential(session, secret_store, bob, "fakeprov", "api_key")


def test_lease_is_ephemeral_and_never_serializable():
    lease = CredentialLease({"api_key": SENTINEL, "org_token": "other"})
    assert lease.get("api_key") == SENTINEL
    assert lease.get("org_token") == "other"
    with pytest.raises(CredentialMissingError):
        lease.get("missing")
    # Repr carries no material and no kind vocabulary.
    assert SENTINEL not in repr(lease)
    assert "api_key" not in repr(lease)
    with pytest.raises(TypeError):
        pickle.dumps(lease)


def test_lease_materializes_plural_required_credential_kinds(session, secret_store):
    actor = _actor(session)
    services.provision_credential(session, secret_store, actor, "fakeprov", "api_key", SENTINEL)
    services.provision_credential(session, secret_store, actor, "fakeprov", "org_token", "org-value")

    lease = build_credential_lease(session, secret_store, actor, "fakeprov", ("api_key", "org_token"))
    assert lease.get("api_key") == SENTINEL
    assert lease.get("org_token") == "org-value"


def test_missing_secret_material_fails_explicitly_without_echoing_it(session, secret_store):
    actor = _actor(session)
    binding = services.provision_credential(session, secret_store, actor, "fakeprov", "api_key", SENTINEL)
    # Simulate material that vanished from the store while the binding remains.
    secret_store.delete(binding.secret_ref)

    with pytest.raises(SecretMissingError) as exc_info:
        build_credential_lease(session, secret_store, actor, "fakeprov", ("api_key",))
    assert SENTINEL not in str(exc_info.value)


def test_no_secret_column_exists_on_binding_table(session):
    from sqlalchemy import inspect

    columns = {column["name"] for column in inspect(session.get_bind()).get_columns(
        ExternalProviderCredentialBinding.__tablename__
    )}
    assert "secret" not in columns
    assert "token" not in columns
    assert "password" not in columns
    assert "secret_ref" in columns
