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
ProjectResourceLink      the set of global resources (objects/references) a Project's context includes + the visibility lens (see ADR-0008)
ResourceStewardship      which steward Project (if any) may MUTATE a global resource — visibility is not stewardship
MutationGrant            Core shared authority primitive: a pre-validated authority token
                         (resource_id, steward_project, actor, role, purpose) issued to a
                         domain command — SO/EP accept it without importing Identity
ResourceOwnership        the creating Actor recorded as audit/provenance (no full ACL)
ExternalProviderCredentialBinding  a per-(Actor, provider, credential kind) non-secret binding row authorizing a Driver; the secret material lives in the Secret store
```

**Decisions:**
- **Durable identity is an opaque UUID** — never a filesystem path, never an auth
  username.
- **Visibility is not stewardship.** `ProjectResourceLink` grants *read/context*
  visibility only. Mutation of a global resource (rename, append revision, add
  external identity, archive) requires `ResourceStewardship` — a separate grant naming
  the steward Project. **Creating a global provenance edge** follows the **per-edge
  creator contract in `SCIENTIFIC_GRAPH.md`** (steward(source) for #1–4; task-submission /
  import / provider authority for #5–7), *not* a blanket stewardship of both endpoints.
  A non-steward Project's link is read-only by default.
- **Creation authority vs mutation stewardship (round 6):** creating a new global
  resource does **not** pre-exist a stewardship grant (that would be circular) — a
  **mutation-capable membership** (`owner`/`member`, never `viewer`) in a Project
  atomically creates, in one transaction:
  `ScientificObjectSeries` + initial `ScientificObjectRevision` +
  `ProjectResourceLink(Project, series)` + `ResourceStewardship(Project, series)`.
  Thereafter, append-revision / rename / archive require that stewardship grant; global
  edges require the per-edge authority (see `SCIENTIFIC_GRAPH.md`).
- **Acting-Actor mutation formula (round 6, derived at the command boundary):**

  ```text
  can_mutate(actor, project, resource)
      = ProjectMembership(actor, project).role ∈ {owner, member}
        AND ResourceStewardship(resource).steward_project == project
        AND project.deleted_at IS NULL
  ```

  A `viewer` in the steward Project cannot mutate; a member of a non-steward Project
  cannot mutate even if it holds a read link. **Transferring or freezing
  `ResourceStewardship` is higher authority than ordinary mutation** — it changes who
  controls a global resource, so it requires the steward Project's **owner** plus
  explicit approval (the same policy as a Project-sharing change), never a plain
  `owner`/`member` mutation.
- **Who enforces it (round 6):** the application command layer asks Identity to issue a
  **`MutationGrant`**; `Scientific Object` and `Evidence/Provenance` domain commands
  accept the already-validated grant and therefore **do not import Identity** — so they
  remain free of any Identity dependency in the DAG while the enforcement point is
  still explicit.
- **Access is inherited from Project membership** — one authorization vector.
  Per-object ACL is out of scope.
- **Sharing happens at the Project level** (add a member, or a read link for another
  Project). Individual objects/artifacts are not shared outside the Project in this
  slice.
- **Visibility:** `private` (default) | `shared-with-members`. `public` is deferred.
- **Avoid embedding REvoCompute's user model:** REvoLab's Actor is an opaque UUID
  known to Core; external identity is `(authority, native_id)`, and credentials are
  Actor-scoped (`ExternalProviderCredentialBinding`). Core never imports or duplicates
  REvoCompute's Actor/Project model.

**Deferred:** real authentication, RBAC engine, per-object ACL, public sharing.

---

## Persistence (PostgreSQL semantics are the truth)

PostgreSQL is the durable store. Do **not** treat SQLite as architecture truth.

**Dedicated relational tables (indexed, FK integrity):**
- projects, project_memberships, actors, authentication_identities,
  external_provider_credential_bindings
- global_resource_registry (thin referential identity spine — see below)
- scientific_object_series + scientific_object_revision + per-type extension tables
- global provenance edges + project knowledge edges (ownership/lifecycle frozen;
  physical shape + uniqueness deferred to the Phase-1 spike — see
  `SCIENTIFIC_GRAPH.md`)
- run_references, artifact_references, session_references, literature_references,
  external_references (neutral external identity cards)
- evidence, decisions, decision_evidence (+ `cited_as`)

**JSON where appropriate, dangerous where not:**
- **Appropriate:** validated type metadata, Evidence summary, Decision next_actions
  — validated at the API boundary by Pydantic.
- **Dangerous:** foreign keys, stable identity, provider/external refs, relation
  endpoints, or any field that becomes a query filter. JSON must never hold those.

**Indexing:** index all FK columns. Global records (ScientificObject series/revision,
references, `GlobalProvenanceEdge` rows) index their own columns — **no
`(project_id, relation_type)` composite**, because `GlobalProvenanceEdge` carries no
`project_id`. Project-scoped records (`project_resource_link`, `ProjectKnowledgeEdge`,
`evidence`, `decision`) index their `project_id`. `external_identity` enforces
`UNIQUE(authority, native_id)`; `external_reference.external_identity_id` is indexed for
reference/cache lookup (ExternalReference re-stores no identity columns).
Use PostgreSQL enums or CHECK constraints, not free `String(50)` where the domain is
closed.

## Global resource vs project-local context (the Project-boundary persistence answer)

This is the definitive resolution of ADR-0008 at the persistence level. It removes
the ambiguity the reviewer flagged (objects "global" on paper but `→ projects` FK
everywhere).

**Two classes of record, with different ownership and deletion semantics:**

| Class | Records | Identity | Owned by | Project deletion |
|---|---|---|---|---|
| **Global resource** | ScientificObject (series + revision), RunReference, SessionReference, ArtifactReference, LiteratureReference, ExternalReference, and **global provenance edges** (`GlobalProvenanceEdge`, #1–7) | global UUID, project-independent | no single Project owns these; they may be referenced by many Projects | NOT deleted; a Project merely stops referencing them |
| **Project-local context** | Project record, ProjectResourceLink (incl. its folder/container placement), Project annotation/visibility | project-scoped | the Project | **Project record → tombstone** (`deleted_at`); ProjectResourceLink/annotation → hard-delete (they are links/perms) |
| **Project-scoped scientific records** | Evidence, Decision, DecisionEvidence, **project knowledge edges** (`ProjectKnowledgeEdge`, #8–10) | project-scoped | Evidence → Evidence/Provenance domain; Decision/DecisionEvidence/`ProjectKnowledgeEdge` → Knowledge/Decision domain (all project-scoped) | **soft-archive** the Project's Evidence/Decisions/DecisionEvidence/ProjectKnowledgeEdge |

**Concrete relational mapping:**

> This is the **logical** target with one frozen referential-integrity decision (the
> `GlobalResourceRegistry` below). The exact physical table shape of *edges* — whether
> `GlobalProvenanceEdge`/`ProjectKnowledgeEdge` share a table or are separate edge-family
> tables, and their uniqueness/supersession key — is **deliberately deferred to the
> Phase-1 executable spike** (see `SCIENTIFIC_GRAPH.md`). What is fixed here is the
> *logical ownership* and the *referential identity spine*.

**Referential identity spine (reviewer round 4): `GlobalResourceRegistry`.** A single
relational column cannot FK polymorphically to seven different global tables based on a
`resource_kind` discriminator. Global resources therefore register their identity in one
thin spine table:

```text
GlobalResourceRegistry(resource_id PK, resource_kind, created_at)
    resource_kind ∈ scientific_object_series | scientific_object_revision
                  | run_reference | session_reference | artifact_reference
                  | literature_reference | external_reference
```

`resource_kind` values are singular **kind labels** (e.g. `run_reference`); the
concrete tables are their plural forms (e.g. `run_references`). Phase 1 locks this
convention for the enum labels.

`resource_id` **is** the primary key of the concrete global row (the `series_id` /
`revision_id` / reference id), and the concrete table's primary key is **both PK and
FK** to the registry:

```text
scientific_object_revision.revision_id   PK + FK → global_resource_registry.resource_id
run_reference.run_id                     PK + FK → global_resource_registry.resource_id
... (same for series / session / artifact / literature / external reference)
```

**Ownership:** `GlobalResourceRegistry` is a **Core shared identity primitive** — not
owned by any single domain, like the opaque-UUID convention or the type registry; it is
governed as a Core identity/migration concern. Each global-resource-writing domain
(Scientific Object; Evidence/Provenance) registers its rows in the registry within the
same transaction.

**Integrity honesty (round 5):** the FK direction guarantees *concrete row ⇒ registry
row exists*. It does **not**, by itself, guarantee *registry row ⇒ exactly one concrete
subtype exists* or *`resource_kind` equals the actual concrete table*. Those two are
**domain-service invariants** (the creating command inserts exactly one concrete row and
one registry row, and sets the kind), optionally reinforced by a composite
discriminator/CHECK/trigger. Do **not** claim the DB alone prevents a dangling or
mis-typed registry row. The registry carries no scientific payload — it is referential
identity only, **not** a God object.

- **Globally owned tables have NO `project_id` FK:**
  `global_resource_registry`, `scientific_object_series`/`scientific_object_revision`
  (+ per-type tables),
  `run_references`, `session_references`, `artifact_references`,
  `literature_references`, `external_references`, and `GlobalProvenanceEdge` rows.
  Their identity is a global UUID.
- **Project membership of global resources is a separate join, not a column on the
  resource:** `project_resource_link (project_id, resource_id FK →
  global_resource_registry.resource_id, folder?, preferred_revision_id?,
  annotation?)` — **it does NOT store `resource_kind`**: `resource_id` already globally
  identifies the row, and the kind is obtained by joining the registry when needed (a
  second `resource_kind` column would be a second, denormalized copy of the same truth).
  The link also carries the project-local **folder/container placement** and any
  annotation. **There is no `role?` on the link**: `ProjectMembership.role` is the single
  authorization role truth, and `ProjectResourceLink` grants read/context visibility
  only — never a per-resource role (that would be a second ACL). Any future *scientific*
  qualifier (target / reference / control / candidate) is a separate, non-authorizing
  field if and when a real need appears — not `role`. `preferred_revision_id` is the
  optional **project-local pin** of which revision this Project prefers — it is stored on
  the link, not on the global series, and must point at a revision already visible
  through this Project's links. A global
  resource belongs to a Project's context *because a link row exists*, not because the
  resource row can only live in one Project. This is what lets one object/reference sit
  in many Projects. **Series vs revision (reviewer round 3):** linking a series exposes
  the series record but does **not** auto-expose its revisions — a specific immutable
  revision is visible to a Project only through a `scientific_object_revision` link, so
  a new private revision is never auto-visible to a Project that only links the series
  (see ADR-0008 and the visibility closure below).
- **Revision ⇒ series visibility closure (reviewer round 5, frozen):** a revision
  cannot be displayed without its owning series (label, `object_type`, aliases, and
  external IDs all live on the series). Therefore:

  ```text
  revision visible  ⇒  owning series visible
  series visible    ⇏  revisions visible
  ```

  Concretely: creating or retaining a `scientific_object_revision` link **requires**
  the owning series to be linked too (the authorization projection also derives the
  owning series for free from the revision link). The reverse is **never** automatic —
  linking a series exposes **no sibling revisions**, past or future.

  **No `current_revision_id` on the global series (round 5):** the series carries no
  current-revision marker, because a global "current" would leak the existence of
  revisions a Project cannot see and would be a second (derivable) version pointer.
  "Current" is always computed:

  ```text
  latest global revision          = max(revision_seq) over the series
  current visible revision in P   = max(revision_seq) over P's visible revisions
                                   (or the Project's preferred_revision_id pin, if set)
  ```
- **Special case — `ProjectMembership` (the Actor-in-Project row)** is a separate
  join over `actors × projects` and is the security unit; `ProjectResourceLink` is the
  context/visibility lens over **global resources**. They are distinct and both live at
  the Project boundary.
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

**Project deletion = tombstone, not a hard delete (SQL-valid; see ADR-0008):**
1. **Tombstone** the Project row (`project.deleted_at != NULL`) so the
   `evidence.project_id` / `decision.project_id` FKs stay valid and the Project keeps
   its minimal identity/framing. **Never hard-delete the Project row.**
2. **Hard-delete** active membership rows and `ProjectResourceLink` rows (access/
   context/placement state, safe to remove).
3. **Soft-archive** the Project's Evidence, Decisions, DecisionEvidence, and
   `ProjectKnowledgeEdge` rows.
4. **Global objects, references, and `GlobalProvenanceEdge` rows are untouched** — they
   survive, may remain attached to other Projects, and their provenance stays
   traversable.

**Referential integrity:** FKs from project-scoped tables → `projects`; from
project-scoped Evidence/Decision → referenced global objects/references (never
CASCADE to a global object). FKs from `ProjectResourceLink.resource_id` →
`global_resource_registry.resource_id` (single-column FK, no polymorphic FK), and
every concrete global table's primary key is **both PK and FK** to that same registry
row. **Blanket CASCADE to global resources is removed** (see Lifecycle).

### Authorization projection: global identity != global readability

The durable storage graph is **global**. Readability is always **Project-mediated** and
computed per query — it is never an attribute of the stored row.

```text
storage graph:
    global resources + global provenance (durable record)

authorization projection (computed per query):
    Actor → ProjectMembership → ProjectResourceLink → visible resources
                                                   → visible provenance edges
```

- An actor reads **through a Project** (their membership grants that Project's lens).
- A global resource is visible through a Project **iff** it is linked by that Project's
  `ProjectResourceLink`.
- A provenance **edge** appears in a Project's graph projection **only if the current
  actor can see every endpoint it connects** (all endpoints visible through that
  Project's links, or themselves this Project's project-scoped Evidence/Decision).
  Partially-privileged edges are **not** shown — no partial leak.

**Write-time visibility invariant (reviewer round 5):** readability filtering is not
enough on its own — it would allow **ghost knowledge**: a Project-scoped row that
references a global endpoint the Project itself cannot see, hidden at read time but still
persisted. Therefore, at **write time**, for every project-scoped row
(`Evidence`, `Decision`, or `ProjectKnowledgeEdge`):

```text
every global endpoint it references
    MUST already be visible through that Project's ProjectResourceLink set.

Evidence.source / Evidence.target  → same rule (a target Revision implies its
                                    owning Series is visible via the closure).
Decision --selects (Series|Revision) → the permanent target must be linked.
Decision --cites (Evidence)           → the cited Evidence is same-Project (already).
```

This is a **domain invariant** enforced by the command service, not merely a query-time
projection behavior — the graph must never contain project knowledge whose global
anchors the Project cannot see.

**Write side (stewardship, not projection):** the read projection above governs
*visibility only*. *Mutation* of a global resource (rename/revision/external-id/archive)
requires `ResourceStewardship` over that resource. A global provenance edge is created
**only** through its typed domain operation with the **per-edge creator authority** from
`SCIENTIFIC_GRAPH.md` (which is `steward(source)` for #1–4, task-submission / import /
provider authority for #5–7) — run import → `produced`; import command → `imported_as`;
task submission → `consumed_as_input_by`; object commands →
`variant_of`/`represents`/`derived_from`/`evaluates` — never a generic global relation
writer. **Visibility is not stewardship.**

(Read-time projection example: Project B links global object `X` but not `Y`, a private
RunReference. The storage graph still holds `X --consumed_as_input_by--> Y`, but
Project B's projection hides that edge because endpoint `Y` is invisible to B — no
partial leak. The derived `generated_by` aggregate over `produced`+`imported_as` is
computed the same way only when all its endpoints are visible. Revoking a
`ProjectResourceLink` removes visibility immediately with no migration.)

**Deletion:** soft-delete/archive for scientific content; hard-delete for
links/memberships; immutable records never hard-deleted. Use a `deleted_at`/null
timestamp + partial unique index to keep uniqueness valid across soft-deletes.

**Versioning:** scientific objects are versioned via revisions
(`SCIENTIFIC_OBJECT_MODEL.md`); a committed Decision is never edited or deleted — it is
superseded by a newer Decision through the `supersedes` `ProjectKnowledgeEdge` (#9),
with `superseded` a derived status. Not a full audit table per entity.

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
| Project | project-local | yes | metadata/visibility | no | **tombstone** (`deleted_at`) | **never hard-delete the row**; delete active links/membership/placement; archive its Evidence/Decision/knowledge edges; global resources untouched |
| ProjectResourceLink | project-local | yes | folder/preferred_revision/annotation | n/a | n/a | hard-delete safe (context/access/placement link — no per-resource role) |
| ResourceStewardship | project-local (steward Project) | yes | transfer / freeze | n/a | n/a | hard-delete on explicit transfer; on steward-Project tombstone the resource **freezes** (no mutation) until transferred |
| ScientificObject | **global** | yes | label/metadata while draft (stewardship-gated) | **yes — revision (see SCIENTIFIC_OBJECT_MODEL)** | soft (once referenced) | global object: blocked if referenced; else archived, never hard-deleted |
| GlobalProvenanceEdge (#1–7) | **global** | yes | **never** | n/a | n/a | **never** — a provenance node; correct by superseding edge |
| ProjectKnowledgeEdge (#8–10) | **project-scoped** | yes | **never** | n/a | soft-archive with its Decision/Evidence | archive with its Decision/Evidence on Project tombstone; never hard-delete the row |
| Run/Artifact/Session/Lit/ExternalReference | **global** | once | **never** (identity immutable) | n/a | revoke (mark `revoked_at`/broken) | never — provenance stays traversable |
| Evidence | project-scoped | yes | only interpretive fields (not identity/source/target) | n/a | soft | only if no citing Decision; else archive |
| Decision | project-scoped | yes (draft) | status while draft | n/a | soft | **never** once committed — supersede |
| Membership | project-local | yes | yes | n/a | n/a | hard-delete allowed |
| Credential binding (`ExternalProviderCredentialBinding`) | Actor-scoped (no `project_id`; global binding) | yes | yes (rotate `secret_ref`) | n/a | n/a | hard-delete allowed on revocation |

**Unique global resource rule:** Global resources (ScientificObject series/revision,
all reference nodes, `GlobalProvenanceEdge` rows) have **no `project_id` owner**; they
can be referenced by many Projects and are **never deleted by any Project** — only
archived or revoked. They live as long as their global identity survives.

**Unique project-scoped rule:** Evidence, Decision, and `ProjectKnowledgeEdge` rows are
authored in and scoped to one Project. Deleting a Project **soft-archives** its
Evidence/Decisions/DecisionEvidence/ProjectKnowledgeEdge; it **never touches** the
global objects/references they reference or the global provenance edges linking them.

**Deleting a Project (definitive sequence):**
1. **Tombstone** the Project row (`deleted_at != NULL`) — never hard-delete it — so
   `project_id` FKs on Evidence/Decision stay valid and the minimal Project framing
   survives for audit.
2. Hard-delete the Project's membership rows and `ProjectResourceLink` rows (incl.
   their folder/container placement annotation).
3. Soft-archive the Project's Evidence, Decisions, DecisionEvidence, and
   `ProjectKnowledgeEdge` rows.
4. For any global resource this Project **stewards**: transfer `ResourceStewardship` to
   another steward Project, or **freeze** the resource (no further mutation) — never
   delete the resource.
5. Leave all global objects, references, and `GlobalProvenanceEdge` rows untouched —
   they survive, may remain attached to other Projects, and their provenance stays
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
  `decision_evidence` join, the Decision `status` field (`draft | committed`), and the
  `supersedes` edge (`superseded` derived).
- **Keep now:** `created_at`/`updated_at`/`created_by` on every science table (the
  durable audit substrate); immutable provenance rows; the `DecisionEvidence` join
  (records EvidenceAttached); Decision status (records transitions).
- **Future trigger (do not build now):** when notifications or cross-project activity
  feeds are a demonstrated requirement, add a single append-only `domain_events`
  table written in the **same transaction** as the mutation — still not event
  sourcing (the entity rows remain the source of truth; the event table is an
  audit/projection aid).

Recorded in **ADR-0008–0014** as applicable.
