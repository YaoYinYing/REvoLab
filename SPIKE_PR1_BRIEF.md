# SPIKE PR1 — Converge the Contract (Brief) — Round 3

Save for a fresh agent / new round. Branch: `docs/architecture-design`. PR #1: open,
mergeable, head as of writing `93544af` (`9dab54e` + cleanup). Executable gates are green
(GHA run #14 all green; backend pytest, frontend typecheck/test/build, PostgreSQL 16
`alembic upgrade head && alembic check`).

## Objective (verbatim)

Address the third-round human review (REQUEST CHANGES) of PR #1 on branch
docs/architecture-design (head 93544af) to converge the four core blockers and remaining
findings, then run the final fresh architecture review. The reviewer's four core blockers:
(1) uniquely assign ProjectResourceLink ownership (Project owns Project + ProjectResourceLink;
Identity owns Actor + ProjectMembership + Role; authorization projection =
Actor->ProjectMembership[Identity]->Project->ProjectResourceLink[Project]->visible resource,
so Phase 1 can do Project-context composition without full auth);
(2) close series/revision visibility (define whether linking a series auto-exposes all
revisions; use distinct resource_kinds scientific_object_series vs scientific_object_revision
or pinned-visible-revisions so a new private revision is not auto-visible to other Projects);
(3) add a scope column to the canonical edge matrix in SCIENTIFIC_GRAPH (edges #1-8
variant_of/derived_from/consumed_as_input_by/produced/imported_as/generated_by = global;
edges #9-11 selects/supersedes/cites = project-scoped, archive with Decision/Evidence on
Project tombstone);
(4) fix the Evidence.source contract contradiction (not 'exactly one required' + 'nullable
for observation' — choose 0..1 with required-for computation/literature/imported and
optional-for direct-observation/note, or a real project-scoped NoteRecord source).
Additionally:
(5) fix ScientificObject physical model so typed payload hangs on ScientificObjectRevision not
series (series table holds series_id/object_type/title/current_revision_id; revision table
holds revision_id/series_id/revision_seq/checksum; typed protein_revision/structure_revision
FK to revision_id);
(6) consolidate credential ownership (Identity owns ExternalProviderCredentialBinding
(actor_id,provider_key,kind,secret_ref), Secret/Credential store owns secret material,
Provider consumes opaque handle, Agent owns Tool projection; stop claiming CredentialBinding
is a fake abstraction);
(7) relax ExternalId uniqueness to ExternalIdentity UNIQUE(authority,native_id) with a
separate non-global-1:1 mapping series_id->external_identity_id + qualifier/role;
(8) clean stale contracts in COLLABORATION_IDENTITY (remove relation (project_id,relation_type)
index since global Relations have no project_id; fix Project-local context
hard-delete-safety label since Project is always tombstoned);
(9) add a Harness runaway guard (every autonomous Goal has finite round budget, explicit
acceptance gates, explicit non-goals, no-progress detector, permission ceiling;
stop-and-ask-human triggers);
(10) fix subagent composition contract (Role/Question/Allowed paths/Write permission/
Evidence required/Output schema/Stop condition; no recursive subagent spawning by default,
bounded concurrency, no permission inheritance expansion);
(11) classify package install in the safety plane (install existing locked/project-declared
deps = normal; add/update dependency, install arbitrary package, execute unreviewed
install/postinstall scripts = approval);
and update IMPLEMENTATION_STATE CI evidence to the current head (93544af) not d7bc7fe.
Keep the PR honest as Proposed-pending-human-review and mergeable; run the final fresh
architecture review (the reviewer will approve/merge if no new P1).

## Status notes

Third human review: REQUEST CHANGES, "don't merge yet", direction now ~90%. Core blockers
(1)-(4) plus findings (5)-(11) must converge, then ONE final fresh architecture review.
If that finds no new P1, the reviewer will approve and merge PR #1, then DSH proceeds to
Phase 1 scientific-context core migration.
