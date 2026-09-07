# ADR-0012: Provider / Driver / Capability Architecture

## Context
The bootstrap `drivers.py` has a single `Driver` Protocol with free-string
`capabilities() -> set[str]` and invented lifecycle states, and **no credential
concept at all**. The design brief requires distinct capabilities per integration
semantics, provider-vocabulary isolation, and a way for a project to know a provider
is available.

## Decision
- **Five concepts:** Provider (stable slug key), Driver (realizes capabilities),
  Capability (Core-owned kind + protocol), Tool (agent-facing bridge), Credential
  (owned by the credential store). `CredentialBinding` is **not** introduced (a
  `has_credential` presence lookup suffices). `ExternalReference` is a stored data
  shape in the Evidence domain, not a driver concept.
- **Distinct capability Protocols:** `ComputeCapability` (batch), `SearchCapability`
  (literature + biological lookup), `ArtifactResolutionCapability` (pure read),
  `DesignCapability` (export half) + `InteractiveHandoffCapability` (interactive
  half). REvoDesign is stateful, so it is NOT forced into Compute.
- **Capability identity = `(provider_key, kind)`** where `kind` is a Core-owned
  closed enum; Core discovers by kind, never by provider vocabulary.
- **Schema-as-data:** capability methods declare input/output JSON Schema; Core
  validates against the provider's own schema and assigns no meaning to its fields.
- **Credentials:** owned by the credential store, **scoped per Actor**
  (`(actor_id, provider_key, kind)`); every capability call takes a `credential`
  handle for the calling actor; **availability is a derived, Actor-contextual probe** —
  `available(actor, provider) = driver READY AND required credential kinds present for
  the actor AND project policy permits` — never stored.
- **Runtime health and availability are separate coeffects (reviewer finding #6):**
  `ProviderRuntimeHealth` (`READY | DEGRADED | UNREACHABLE`) is per-provider and
  actor-independent and is the **only** per-provider registry state; `Capability
  Availability(actor, project)` (`AVAILABLE | CREDENTIAL_MISSING | NOT_AUTHORIZED |
  PROVIDER_UNAVAILABLE`) is a **derived projection**, never stored. `credential_missing`
  is never a provider property — it is always per-Actor.
- **Authority is not transport (reviewer finding #8):** durable external identity is
  an identity `authority/namespace` (uniprot, pdb, doi, pubmed; `revocompute` is both
  authority and provider for its own IDs), kept separate from the resolver/provider
  (openbio, direct APIs). Changing the resolver never changes the identity.
- **Failure:** typed `CapabilityError` with a stable kind enum; no silent fallbacks;
  an unreachable provider degrades to "unverifiable" references, never corruption; no
  hot-unloading.
- **ADR-0004 revisions:** keep Protocol + scoped registry + entry points + start/
  stop rollback; split Driver into lifecycle + capability realization; replace
  `capabilities() -> set[str]` with a typed kind→instance map; collapse 5 lifecycle
  states into 2 domain-visible states (REGISTERED/READY); add the credential concept.

## Consequences
- REvoCompute, REvoDesign, and OpenBio integrate behind capability Protocols with no
  special-case Core logic and no vocabulary leak.
- Credential revocation needs no migration (availability is a query).
- Frontend and Agent discover through one read-only Provider Catalog.

## Rejected alternatives
- One generic interface for all integrations (fails stateful REvoDesign / REvoCompute
  distinction).
- CredentialBinding entity (one-use-case fake abstraction).
- Provider-specific branches in Core.
