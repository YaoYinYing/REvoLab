# SPIKE PR1 — Converge the Contract (Brief)

Save for a fresh agent / new round. Branch: `docs/architecture-design`. PR #1: open,
mergeable, head as of writing `d7bc7fe`. Executable gates are green (GHA: backend
pytest, frontend typecheck/test/build, PostgreSQL 16 `alembic upgrade head && alembic
check`).

## Objective (verbatim)

Revise PR #1 (branch docs/architecture-design, head d7bc7fe) to address the reviewer's
second round (REQUEST CHANGES):

(1) define the project-scoped authorization projection for global resources &
provenance — global identity != global readability, Actor -> ProjectMembership ->
ProjectResourceLink -> visible edges — write it into ADR-0008/COLLABORATION_IDENTITY
and rename ProjectObjectMembership to ProjectResourceLink;

(2) fix Project deletion to be SQL-valid (tombstone Project with deleted_at, hard-delete
active membership links, archive Evidence/Decision, leave global resources untouched —
never hard-delete a Project row);

(3) make SCIENTIFIC_GRAPH.md the single source of graph truth — unify the wire value
(consumed_as_input_by vs consumed_input_by), fix the SYSTEM diagram direction, remove
from IMPLEMENTATION_ROADMAP the stale UNIQUE(project,...) and the "Generic Relation
target type: Decided" claim so Roadmap only references SCIENTIFIC_GRAPH;

(4) add an Evidence association contract (freeze legal Evidence.source / Evidence.target
kinds incl. experiment/note as source, plus cardinality and immutability);

(5) define A-->B as "imports/consumes B's public contract" and regenerate ONE dependency
DAG consistent across DOMAIN_BOUNDARIES/SYSTEM_ARCHITECTURE (fix SO->Project, Project
"membership links", Provider owning credentials/tools);

(6) split ProviderRuntimeHealth (READY/DEGRADED/UNREACHABLE) from
CapabilityAvailability(actor,project) (AVAILABLE/CREDENTIAL_MISSING/NOT_AUTHORIZED/
PROVIDER_UNAVAILABLE, derived, never stored);

(7) align HARNESS_OPERATING_MODEL step 7 to "INDEPENDENT REVIEW" with Ralph only on
explicit human request;

(8) add container-engine safety guardrails (docker = privileged external capability, not
a workspace write) to the safety plane;

(9) freeze ScientificObject Series.id/Revision.id nomenclature (no ambiguous
ScientificObject.id);

(10) make AGENT_CONTEXT + Harness skill list match the real .agents/skills (create
architecture-review + security-boundaries or mark Planned);

(11) update IMPLEMENTATION_STATE "not verified" to verified (GHA green);

(12) delete the 5 superseded pointer files (overview/domain-model/drivers/
evidence-and-lineage/agent-and-skills).

Then run a fresh architecture review pass; keep the PR honest as
Proposed-pending-human-review and mergeable.

## Status notes

Reviewer still REQUEST CHANGES, "don't merge yet". Direction ~80–85% right. After the
12 items converge, run ONE fresh architecture reviewer; if it finds no new
cross-document ownership/security contradiction, PR #1 is approvable.
