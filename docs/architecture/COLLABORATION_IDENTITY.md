# Identity, Collaboration, Persistence, Lifecycle & Audit

> **Status: Proposed — pending human architecture review.** Reconciles
> identity/sharing, persistence, lifecycle/deletion,
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
`authority + native_id` for external-reference lookup. Use PostgreSQL enums or
CHECK constraints, not free `String(50)` where the domain is closed.

## Global resource vs project-local context (the Project-boundary persistence answer)

This is the definitive resolution of ADR-0008 at the persistence level. It removes
the ambiguity the reviewer flagged (objects "global" on paper but `→ projects` FK
everywhere).

**Two classes of record, with different ownership and deletion semantics:**

| Class | Records | Identity | Owned by | Project deletion |
|---|---|---|---|---|
| **Global resource** | ScientificObject (series + revision), RunReference, SessionReference, ArtifactReference, LiteratureReference, ExternalReference, and provenance Relation records | global UUID, project-independent | no single Project owns these; they may be referenced by many Projects | NOT deleted; a Project merely stops referencing them |
| **Project-local context** | Project record, ProjectObjectMembership, Project annotation/visibility | project-scoped | the Project | hard-delete safe (these are links/perms) |
| **Project-scoped scientific records** | Evidence, Decision, DecisionEvidence | project-scoped | authored in, and scoped to, one Project | **soft-archive** the Project's Evidence/Decisions/DecisionEvidence |

**Concrete relational mapping:**

> This is the **logical** target. The exact physical table shape — notably whether
> provenance `relations` is one polymorphic table or several edge-family tables, and
> its uniqueness/supersession key — is **deliberately deferred to the Phase-1
> executable spike** (see `SCIENTIFIC_GRAPH.md`), per the reviewer's finding that the
> physical Relation design should not be frozen before an implementation fork. What is
> fixed here is the *logical ownership*: global records are not project-owned.

- **Globally owned tables have NO `project_id` FK:**
  `scientific_objects` (+ per-type tables), `run_references`, `session_references`,
  `artifact_references`, `literature_references`, `external_references`, and the
  provenance `relations` records. Their identity is a global UUID.
- **Project membership is a separate join, not a column on the object:**
  `project_object_membership (project_id, scientific_object_id, role, annotation)`.
  An object belongs to a Project *because a membership row exists*, not because the
  object row can only live in one Project. This is what lets one object sit in many
  Projects.
- **Evidence / Decision are project-scoped**: their rows carry `project_id` and are
  validated same-project (as today). A claim (Evidence) and a conclusion (Decision)
  are an interpretation made *in the context of a Project* by its members.
- **Cross-project sharing of a claim**: Projects do not share Evidence/Decision rows.
  They share the underlying **global** object/reference, and each Project forms its
  own Evidence/Decision interpretation of it. (This is consistent with the graph
  model where Evidence/Decision are project-scoped and contradictory Evidence across
  Projects is not just legal but expected.)

**Why Evidence/Decision are project-scoped (not global):** a claim or conclusion is
meaningless without the framing Project's question, membership, and prior context.
Making them global would couple one Project's interpretation to another's and make
Project deletion unable to clean up. The immutable global objects and references the
claims cite remain untouched and shared.

**Project deletion semantics (unique answer):**
1. Hard-delete the Project record + membership rows + project annotation (permissions/links, safe to remove).
2. **Soft-archive** the Project's Evidence, Decisions, and DecisionEvidence (they have no life outside the deleted Project; archiving preserves auditable history).
3. **Global objects, references, and provenance relations are untouched** — they survive, may remain attached to other Projects, and their provenance stays traversable.

**Referential integrity:** FKs from project-scoped tables → `projects`; from
project-scoped Evidence/Decision → referenced global objects/references (never
CASCADE to a global object). FKs from membership → the global `scientific_objects`
table. **Blanket CASCADE to global resources is removed** (see Lifecycle).

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
survive merely because it was easy.** Replace it with explicit lifecycle rules per the
three classes established above (global resource / project-local context /
project-scoped scientific record):

| Entity | Class | Create | Update | Version | Archive | Delete |
|---|---|---|---|---|---|---|
| Project | project-local | yes | metadata/visibility | no | soft | hard-delete record + links + annotation; **never** global objects |
| ProjectObjectMembership | project-local | yes | role/annotation | n/a | n/a | hard-delete safe (it is a link) |
| ScientificObject | **global** | yes | label/metadata while draft | **yes — revision (see SCIENTIFIC_OBJECT_MODEL)** | soft (once referenced) | global object: blocked if referenced; else archived, never hard-deleted |
| Relation (provenance) | **global** | yes | **never** | n/a | n/a | **never** — a provenance node; correct by superseding relation |
| Run/Artifact/Session/Lit/ExternalReference | **global** | once | **never** (identity immutable) | n/a | revoke (mark `revoked_at`/broken) | never — provenance stays traversable |
| Evidence | project-scoped | yes | only interpretive fields (not identity/source/target) | n/a | soft | only if no citing Decision; else archive |
| Decision | project-scoped | yes (draft) | status while draft | n/a | soft | **never** once committed — supersede |
| Membership / credentials | project-local | yes | yes | n/a | n/a | hard-delete allowed |

**Unique global resource rule:** Global resources (ScientificObject series/revision,
all reference nodes, provenance Relations) have **no `project_id` owner**; they can be
referenced by many Projects and are **never deleted by any Project** — only archived or
revoked. They live as long as their global identity survives.

**Unique project-scoped rule:** Evidence and Decision are authored in and scoped to one
Project. Deleting a Project **soft-archives** its Evidence/Decisions/DecisionEvidence;
it **never touches** the global objects/references they reference or the provenance
relations linking them.

**Deleting a Project (definitive sequence):**
1. Hard-delete the Project record, its membership rows, and its annotations/visibility.
2. Soft-archive the Project's Evidence, Decisions, and DecisionEvidence.
3. Leave all global objects, references, and provenance Relations untouched — they
   survive, may remain attached to other Projects, and their provenance stays
   traversable.

A Protein cannot be hard-deleted if Evidence references it (block → archive). A
committed Decision cannot be edited or deleted — supersede instead. References are
never deleted, only revoked.

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
