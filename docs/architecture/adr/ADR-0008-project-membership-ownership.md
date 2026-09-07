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
`LiteratureReference`, `ExternalReference`), and provenance Relations are **global** (no
`project_id` owner, held across many Projects). `Evidence` and `Decision` are
**project-scoped** (authored in and owned by one Project). `Project`, membership, and
annotation are project-local. Nothing is owned by a Project through a cascade.

**`ProjectResourceLink` renames `ProjectObjectMembership`** — the global resources linked
into a Project's context are not only ScientificObjects; references are global too. A
`ProjectResourceLink` is:

```text
ProjectResourceLink(project_id, resource_id, resource_kind, role?, annotation?)
```

where `resource_kind ∈ {scientific_object, run_reference, session_reference,
artifact_reference, literature_reference, external_reference}` enforces which global
resource categories a Project's context may contain. It is **not** a per-object ACL; it
is the statement "this Project's context includes these global resources."

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
`X --generated_by--> Y` (where `Y` is a private RunReference not linked into Project B)
is **not** projected for Project B's users, because endpoint `Y` is not visible to them.

**Immutability of who-may-see:** the durable storage graph never changes for
authorization reasons; visibility is a projection computed at read time from current
membership + links. Revoking a link removes visibility immediately with no migration.

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
archive Evidence / Decision / DecisionEvidence (deleted_at on the project-scoped rows)
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
