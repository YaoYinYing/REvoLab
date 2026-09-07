# Identity, Collaboration, Persistence, Lifecycle & Audit

> **Status:** Accepted. Reconciles identity/sharing, persistence, lifecycle/deletion,
> and the event/audit posture from Subagent F.

## Identity & collaboration

Separate these primitives (never conflated):

```text
AuthenticationIdentity   external auth claim (OIDC subject, email) — one per provider, many per Actor; NOT a durable DB key
Actor                    the durable human/agent principal — an opaque stable UUID, independent of any auth provider
Role                     small enumerated set at Project level: owner · member · viewer
ProjectMembership        an Actor(role) within a Project; the security unit
ResourceOwnership        the creating Actor recorded as audit/provenance (no full ACL)
ExternalProviderCredential  a per-(Actor, provider) binding authorizing a Driver
```

**Decisions:**
- **Durable identity is an opaque UUID** — never a filesystem path, never an auth
  username.
- **Access is inherited from Project membership** — one authorization vector.
  Per-object ACL is out of scope.
- **Sharing happens at the Project level** (add a member, or a read link for another
  Project). Individual objects/artifacts are not shared outside the Project in this
  slice.
- **Visibility:** `private` (default) | `shared-with-members`. `public` is deferred.
- **Avoid embedding REvoCompute's user model:** REvoLab's Actor is an opaque UUID
  known to Core; bindings are `(provider, external_id, Actor-scoped credential)`.
  Core never imports or duplicates REvoCompute's Actor/Project model.

**Deferred:** real authentication, RBAC engine, per-object ACL, public sharing.

---

## Persistence (PostgreSQL semantics are the truth)

PostgreSQL is the durable store. Do **not** treat SQLite as architecture truth.

**Dedicated relational tables (indexed, FK integrity):**
- projects, project_memberships, actors, authentication_identities,
  external_provider_credentials
- scientific_objects + per-type extension tables
- relations (unique source/target/relation_type within a project)
- run_references, artifact_references, literature_references (neutral external
  identity cards)
- evidence, decisions, decision_evidence (+ `cited_as`)

**JSON where appropriate, dangerous where not:**
- **Appropriate:** validated type metadata, Evidence summary, Decision next_actions
  — validated at the API boundary by Pydantic.
- **Dangerous:** foreign keys, stable identity, provider/external refs, relation
  endpoints, or any field that becomes a query filter. JSON must never hold those.

**Indexing:** index all FK columns and (project_id, relation_type); index
`provider + external_id` for external-reference lookup. Use PostgreSQL enums or
CHECK constraints, not free `String(50)` where the domain is closed.

**Referential integrity:** FKs from objects/relations/evidence/decisions →
projects; from relations/evidence → objects. **Blanket CASCADE is removed** (see
Lifecycle).

**Deletion:** soft-delete/archive for scientific content; hard-delete for
links/memberships; immutable records never hard-deleted. Use a `deleted_at`/null
timestamp + partial unique index to keep uniqueness valid across soft-deletes.

**Versioning:** version scientific objects and decisions; a `supersedes_id` self-FK
for decisions. Not a full audit table per entity.

**Audit timestamps:** `created_at`, `updated_at` (timestamptz), plus `created_by`
(actor UUID, never a username).

**Migration philosophy:** Alembic, forward-only additive migrations, drift-checked
on BOTH SQLite (dev) and PostgreSQL (CI). No destructive/reordering migrations.

---

## Lifecycle & deletion (overturning blanket CASCADE)

The current bootstrap uses `cascade="all, delete-orphan"` everywhere. **That must not
survive merely because it was easy.** Replace it with explicit lifecycle rules:

| Entity | Create | Update | Version | Archive | Delete |
|---|---|---|---|---|---|
| Project | yes | metadata/visibility | no | soft | soft OR hard-delete links/membership ONLY; **never** underlying objects |
| ScientificObject | yes | label/metadata while draft | yes, on content change | soft (once referenced) | allowed only if NOT referenced; otherwise block → archive |
| Relation | yes | **never** | n/a | n/a | **never** — a provenance node; correct by superseding edge |
| Evidence | yes | only interpretive fields (not identity) | n/a | soft | only if no citing Decision; else archive |
| Run/ArtifactReference | once | **never** (identity immutable) | n/a | revoke (mark `revoked_at`/broken) | never — provenance stays traversable |
| Decision | yes | only while open/draft | n/a | soft | **never** once it influenced later work — supersede |
| Membership / credentials | yes | yes | n/a | n/a | hard-delete allowed |

**Summary rule:**
- **Scientific content** (objects, relations, evidence, references, decisions) =
  soft-archive + immutable-once-referenced; never silently cascade-deleted.
- **Containers & permissions** (Project record, memberships, credentials) =
  hard-deletable.
- **Project deletion removes links; global objects and their provenance survive.** A
  Protein cannot be hard-deleted if Evidence references it; a Decision cannot be
  edited after influencing later work; references are never deleted, only revoked.

---

## Event / audit

**Decision: do NOT build an explicit persisted event/audit infrastructure now. Do NOT
event-source.**

- The listed events (ObjectCreated, RelationCreated, EvidenceAttached,
  DecisionRecorded, ExternalRunLinked, ArtifactImported) are **all derivable** from
  the entity rows themselves: `created_at`/`created_by` on each table, the
  `decision_evidence` join, and the status/supersession fields.
- **Keep now:** `created_at`/`updated_at`/`created_by` on every science table (the
  durable audit substrate); immutable provenance rows; the `DecisionEvidence` join
  (records EvidenceAttached); Decision status (records transitions).
- **Future trigger (do not build now):** when notifications or cross-project activity
  feeds are a demonstrated requirement, add a single append-only `domain_events`
  table written in the **same transaction** as the mutation — still not event
  sourcing (the entity rows remain the source of truth; the event table is an
  audit/projection aid).

Recorded in **ADR-0008–0014** as applicable.
