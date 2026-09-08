# ADR-0008: Global Resource Identity + Project-Scoped Membership

## Context
Cross-user sharing is a stated requirement. A model where "Project owns every object
directly" forces object duplication to share across projects, which is explicitly
forbidden. The current bootstrap makes Project the owner by destructive cascade
(`cascade="all, delete-orphan"`), which would block sharing if left to harden.

A second-round review flagged a real leak: if global resources *and* global provenance
are simply readable by any member of any linked Project, Project A's private execution
details leak into Project B through a shared object. So **global identity must not imply
global readability** — readability is always mediated by the Project the actor reads
through.

## Decision

Use **global/stable resource identity + project-scoped reference/membership** — **not**
"Project owns every object directly."

**Scope split (definitive):** `ScientificObjectSeries`/`ScientificObjectRevision`, all
reference nodes (`RunReference`, `SessionReference`, `ArtifactReference`,
`LiteratureReference`, `ExternalReference`), and **global provenance edges**
(`GlobalProvenanceEdge`, #1–7) are **global** (no `project_id` owner, held across many
Projects). `Evidence`, `Decision`, and **project knowledge edges** (`ProjectKnowledgeEdge`,
#8–10) are **project-scoped** (authored in and owned by one Project). `Project`,
membership, organization placement, stewardship grants, and annotation are
project-local. Nothing is owned by a Project through a cascade.

**`ProjectResourceLink` renames `ProjectObjectMembership`** — the global resources linked
into a Project's context are not only ScientificObjects; references are global too. A
`ProjectResourceLink` is:

```text
ProjectResourceLink(project_id, resource_id FK → GlobalResourceRegistry.resource_id,
                    folder?, preferred_revision_id?, annotation?)
```

`ProjectResourceLink` **does not store `resource_kind`** — `resource_id` already
globally identifies the row, and the kind is obtained by joining the registry (a second
`resource_kind` column would be a denormalized copy of the same truth). It also has
**no `role?` column**: `ProjectMembership.role` is the single authorization-role truth,
and this link grants read/context visibility only — a per-resource `role` would be a
second per-object ACL. `preferred_revision_id` is the **project-local pin** of the
preferred Revision (it must point at a revision already visible through this Project's
links; there is **no** `current_revision_id` on the global series). `resource_id` FKs to a thin `GlobalResourceRegistry(resource_id PK,
resource_kind)` spine that every global resource row registers into (the concrete row's
own primary key, inserted in the same transaction), and every concrete global table's
primary key is **both PK and FK** to that registry row. That FK guarantees *concrete row
⇒ registry row exists*; the reverse (registry row ⇒ exactly one concrete subtype, and
`resource_kind` matching it) is a **domain-service invariant**, optionally reinforced by
a composite discriminator/CHECK/trigger — not claimed as DB-guaranteed. A single
relational column cannot FK polymorphically to seven tables, so the registry provides
one real referential identity target. `ProjectResourceLink` is **not** a per-object ACL;
it is the statement "this Project's context includes these global resources."

**`ResourceStewardship` (round 5) — write authority is separate from visibility.** A
`ProjectResourceLink` grants **read/context** visibility only. Mutating a global
resource (append revision, rename, add external identity, archive, or create a global
provenance edge about it) requires a separate `ResourceStewardship` grant naming the
steward Project. A non-steward Project's link is read-only by default. Deleting the
steward Project does **not** delete the resource: the grant is transferred or the
resource is frozen until transferred. **Visibility is not stewardship.**

**Creation vs mutation (round 6).** Creation cannot require a pre-existing grant (the
resource does not exist yet). A mutation-capable membership (`owner`/`member`) therefore
atomically creates `ScientificObjectSeries` + its initial `ScientificObjectRevision` +
`ProjectResourceLink(Project, series)` + `ResourceStewardship(Project, series)` in one
transaction. Every later mutation of that resource uses the Acting-Actor formula:

```text
can_mutate(actor, project, resource)
    = ProjectMembership(actor, project).role ∈ {owner, member}
      AND ResourceStewardship(resource).steward_project == project
      AND project.deleted_at IS NULL
```

**Enforcement layer.** The application command layer asks Identity to issue a
**`MutationGrant`** (Core shared authority primitive); `Scientific Object` /
`Evidence/Provenance` commands accept the pre-validated grant, so the domain models
need not import Identity — the DAG stays honest without hiding the authorization
dependency.

**Series vs revision visibility (reviewer round 3) + the round-5 closure:**
`scientific_object_revision` and `scientific_object_series` are **distinct** link kinds.
Linking a series exposes the series record but does **not** auto-expose all its
revisions (past or future). A specific immutable revision becomes visible to a Project
only when that Project holds a `scientific_object_revision` link for it (the source of
truth for "is this revision shareable" is the link set, not the implicit series
membership). The frozen closure is one-directional:

```text
revision visible  ⇒  owning series visible
series visible    ⇏  revisions visible
```

Creating or retaining a revision link requires the owning series link to exist (or the
authorization projection derives the owning series for free); sibling revisions are
**never** auto-exposed.

### Global identity != global readability (authorization projection)

Two planes, kept separate:

```text
storage graph:
    global resources + global provenance (the durable record)

authorization projection (computed per query, never stored):
    Actor
      ↓ ProjectMembership
      ↓ ProjectResourceLink
      ↓ visible resources
      ↓ visible provenance edges
```

- An actor **reads through a Project** (their membership grants a Project lens).
- Only global resources linked by a `ProjectResourceLink` of that Project are visible
  through it.
- A provenance **edge appears in a Project's graph projection only when the current
  actor has access to every endpoint the edge connects** (all endpoints must be visible
  through that Project's `ProjectResourceLink` set, or themselves project-scoped records
  of that Project). A partially-privileged edge is **not** shown — no partial leak.

This resolves the leak example: Project B can see the shared object `X`, but the edge
`X --consumed_as_input_by--> Y` (where `Y` is a private RunReference not linked into
Project B) is **not** projected for Project B's users, because endpoint `Y` is not
visible to them. (The derived `generated_by` traversal is projected the same way.)

**Immutability of who-may-see:** the durable storage graph never changes for
authorization reasons; visibility is a projection computed at read time from current
membership + links. Revoking a link removes visibility immediately with no migration.

**Write-time visibility invariant (round 5):** read-time projection is not sufficient —
it would permit *ghost knowledge* (a project-scoped row referencing a global endpoint
the Project itself cannot see, hidden at read time but still persisted). Every
project-scoped write (`Evidence`, `Decision`, `ProjectKnowledgeEdge`) must therefore
verify at write time that **every global endpoint it references is already visible
through that Project's `ProjectResourceLink` set** — a domain invariant, not merely a
projection behavior.

### Project deletion is a tombstone, not a hard delete (SQL-valid)

Earlier phrasing said "hard-delete Project, then archive Evidence/Decision" — that is not
SQL-valid under the FKs `evidence.project_id → project` and `decision.project_id →
project`. The consistent, referentially-valid model:

```text
Delete Project
=
soft-delete (tombstone) the Project row      -> project.deleted_at != NULL
+
hard-delete active sharing links/membership rows
+
archive Evidence / Decision / DecisionEvidence / `ProjectKnowledgeEdge` (deleted_at on the project-scoped rows)
+
for any resource this Project stewards: transfer ResourceStewardship or freeze it
+
leave all global resources and their provenance untouched
```

- The Project **row is never hard-deleted**: `deleted_at != NULL` keeps `project_id`
  FKs valid and preserves the minimal Project identity/framing that
  Evidence/Decision reference (they are otherwise meaningless without it).
- The Project's identity and its archived Evidence/Decision remain as auditable framing;
  the Project no longer appears in any active namespace.
- Global resources survive and stay attached to other Projects.

### Membership links are project-local and hard-deletable

Membership and `ProjectResourceLink` rows are access/context state (not scientific
records), so they are hard-deletable on Project deletion and on revocation.

## Consequences
- Sharing lands without a destructive migration; no data copying.
- Provenance stays traversable across projects and survives Project deletion (the
  storage graph is never rewritten for authorization).
- Readability is always mediated by the Project the actor reads through — a shared
  global object never leaks another Project's private resources/edges.
- Project is simultaneously a namespace, a security boundary, and a weak provenance
  scope, but each concern evolves independently.

## Rejected alternatives
- Project owns every object directly (forces copy-to-share).
- Copying data into each Project on share (explicitly forbidden).
- Global-data, project-readable-implicitly (leaks private resources through shared
  objects — rejected by the authorization projection above).
- Hard-deleting the Project row while keeping `evidence.project_id` FKs (breaks
  referential integrity) — rejected in favor of the tombstone model.
- Letting the current Project CASCADE ownership survive (locks in accidental
  architecture).
