# Project Tool Harness & Analysis Runtime

> **Status: Accepted** (Phase 7). Defines Tool as a first-class Project Harness
> abstraction, the closed Local Tool Runtime, the lightweight/heavyweight execution
> boundary, Tool authority/result semantics, and the single ToolCatalog projection
> shared by the human workspace and the Agent.
>
> This document supersedes the earlier "Tool is owned by the Agent Context domain"
> framing: Tool is now the explicit execution surface of a Project; a
> Driver/Capability is only one implementation detail behind a Tool that crosses an
> external boundary.

## Canonical principle

> **REvoLab owns project context, analysis and interpretation. REvoCompute owns
> heavyweight scientific execution.**

and

> **Tool is a first-class Project Harness concept. Driver is only one possible
> implementation detail behind a Tool.**

Architecture correction (recorded in `IMPLEMENTATION_ROADMAP.md`):

```text
Old assumption:
    REvoLab is a generalized external-provider integration platform.

Corrected assumption:
    REvoLab is a project-centered scientific Harness.
    REvoCompute is its primary heavyweight execution backend.
    External providers are optional implementation details, not the organizing product model.
```

REvoDesign is a method-specific interactive design application and OpenBio is a
design reference — neither is a REvoLab backend and neither is integrated in this
phase.

## Dependency direction (frozen)

```text
Project Harness
      ↓
     Tool
      ↓
implementation adapter
      ↓
local service OR capability/driver
```

A Driver is only needed when a Tool crosses an external system boundary:

```text
inspect_artifact    -> local ArtifactInspection service
table.describe      -> local Analysis runtime
plot.xy             -> local Analysis runtime
record_decision_draft -> typed domain operation
submit_compute      -> REvoCompute capability / driver
```

`revolab.tools` (the Project Tool Harness) depends on the application/domain
services and the Provider/Capability layer; `revolab.agent` and the Presentation
API consume it. It never imports `revolab.agent`, so the Agent remains a consumer
of a lower layer.

## Tool descriptor (canonical)

Every executable Tool carries a stable typed description (TODO.md section 3):

```text
tool_id, name, description
input JSON Schema           (derived from a canonical Pydantic model)
output JSON Schema          (derived from a canonical Pydantic model)
autonomy / authority class  (AgentToolAutonomy: automatic | policy | explicit_action;
                            never_agent = never projected as a Tool)
execution class             (ToolExecutionClass: local | remote)
side-effect class           (ToolSideEffectClass: read_only | creates_derived_result
                            | domain_mutation | external_action)
availability                (derived, never stored)
```

The canonical descriptor is `ToolDescriptorRead` in `revolab/schemas.py`; tool input
models are the SAME Pydantic models the invocation runtime validates against
(`input_schema = input_model.model_json_schema()`), so there is no hand-maintained
copy of a parameter enum or schema.

## Local Tool Runtime

The Local Tool Runtime executes only registered typed tool implementations in a
closed, bounded flow (TODO.md section 5):

```text
ToolRegistry
    ↓ lookup exact tool_id
    ↓ validate input schema (canonical Pydantic input model)
    ↓ authorize Actor/Project/resource access
    ↓ invoke registered implementation
    ↓ validate typed output
    ↓ return ToolResult
```

Never exposed to callers:

```text
eval() / exec(); arbitrary import; arbitrary shell; arbitrary filesystem path;
arbitrary URL fetch; raw SQL; raw HTTP
```

The fixed Phase-7 local tool set (closed; no dynamic discovery):

```text
artifact.inspect        read-only artifact preview
table.describe          bounded per-column statistics over a tabular (CSV) artifact
table.select            bounded column/row projection, optionally persisted
plot.xy                 structured X-Y plot specification, optionally persisted
evidence.create         typed Evidence creation (domain_mutation)
decision.record_draft   typed Decision DRAFT creation (domain_mutation)
decision.commit         Decision draft -> committed (promotion gate, explicit_action)
```

Remote REvoCompute tools are projected through the same catalog
(`{provider}.compute.*`, `{provider}.artifact.resolve`) with
`execution_class=remote`; they execute only through the existing capability
endpoints — the Local Tool Runtime never invokes them.

## Lightweight vs heavyweight execution boundary

**REvoLab local tools** are appropriate for bounded metadata inspection, small table
filtering/summarization, small deterministic transformations, artifact preview,
lightweight comparisons, plot preparation, and evidence/decision/project operations.
Characteristics: short-lived, bounded memory, bounded input, no GPU/HPC, no
containerized scientific package stack, no long-running background execution.
Explicit per-analysis bounds: 1 MiB bytes, 10 000 rows, 100 columns, 5 000 plot
points.

**REvoCompute** is required for GPU workloads, large ML models, MD simulation,
Rosetta, structure prediction, large sequence/model inference, containerized
scientific environments, long-running jobs, resource scheduling, and reproducible
HPC execution. No arbitrary numeric threshold is encoded in Core — semantic rules
plus explicit per-tool limits.

## Invocation contract and result semantics

```text
ToolInvocationRequest { tool_id, input, persist }
    -> resolver -> availability/policy/schema validation
    -> ToolExecution
    -> ToolResult { result_kind, value, resource_id?, resource_kind?, persisted }
```

`ToolResultKind` distinguishes the durable kind of what an invocation produced:

```text
ephemeral           not persisted
artifact            a durable derived ArtifactReference (persist=true)
evidence            a typed Evidence row
decision            a typed Decision (draft or committed)
scientific_object   a ScientificObject / revision
run_reference       an external RunReference (remote submit, via its own endpoint)
```

Persistence semantics are declared by the tool's side-effect class and enforced by
the runtime:

- `read_only` → ephemeral result only.
- `creates_derived_result` → ephemeral by default; `persist=true` (owner/member)
  writes an internal ArtifactReference (ContentStore → reference path) and a
  `ToolInvocation` reproducibility record (tool_id, tool_version,
  input_resource_ids, parameters, result_resource_id). `ToolInvocation` is a
  REvoLab-local activity record, explicitly NOT a `RunReference` (external
  REvoCompute execution identity card). The stored `parameters` are the
  CANONICAL validated model dump (never the raw request dict); the record is
  written in ONE transaction with its derived artifact and is readable through
  `GET /api/projects/{project_id}/tool-invocations`.
- `domain_mutation` → routes through the existing typed domain operations
  (Evidence/Decision). A Decision draft is the only shape the proposal path can
  produce; committing remains the separate authorized promotion gate.
- A ToolResult is **never** automatically promoted to Evidence or Decision truth.

## Authority

One canonical authority truth: `AgentToolAutonomy`.

```text
automatic         safe reads / bounded local analysis
policy            typed domain mutations (evidence, decision draft)
explicit_action   decision commit, external compute submission
never_agent       membership / credential / destructive operations — never projected
```

Catalog availability is derived, never stored: local tools are always listed with a
derived availability (truth/derived-persist tools require owner/member); remote
capability tools are omitted entirely unless their derived availability is
`AVAILABLE` (driver READY + Actor credential presence + project policy permits).

## One catalog for human and Agent

```text
canonical Tool schema
       ↓
Project ToolCatalog   (revolab.tools.catalog.build_tool_catalog)
       ↓
  ┌───────────────┐
  ↓               ↓
frontend         Agent
```

`GET /api/projects/{project_id}/tools` and the Agent's catalog endpoint serve the
same `ToolCatalogRead`. `POST /api/projects/{project_id}/tools/invocations` is the
only invocation surface, and it accepts only registered local tool ids.

## Reproducibility

For any persisted derived result the durable `ToolInvocation` record answers: which
Tool, which version (`1.0.0`), from which input resource identities, with which
typed parameters. Heavy reproducibility belongs to REvoCompute; local tools capture
only that stable, minimal context.

Derived-analysis lineage is deliberately recorded in `ToolInvocation` (the
REvoLab-local activity record), NOT as a `GlobalProvenanceEdge`. The frozen edge
matrix has no artifact→artifact relation, and `SCIENTIFIC_GRAPH.md` ownership would have to
change to add one, so tool derivation is activity/provenance-for-humans, while the
scientific graph keeps only its accepted typed edges.

## Security / resource bounds

The Tool Harness is not a code-execution service. Every local tool has explicit
bounds and fails closed on unsupported formats; tool inputs are untrusted and
validated at the boundary; tool implementations are trusted project code registered
ahead of invocation. Local analysis reads external artifacts only through the
provider's BOUNDED preview path, so a size-less external artifact can never be
materialized beyond the 1 MiB analysis bound.
