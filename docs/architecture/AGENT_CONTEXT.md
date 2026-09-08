# Agent Context & Skills

> **Status: Proposed — pending human architecture review.** Defines the Agent as a
> *consumer* (never an owner) of project
> truth, the context/selection/builder abstractions, the promotion loop, the skills
> architecture, and the safety/authority model.

## The Agent is a consumer, not an owner

Verified invariant (ADR-0002 + the design brief):

> **Agent output is conversation until explicitly promoted into project knowledge via
> a typed, domain-validated operation.**

REvoLab's persistence graph is the durable truth owner. The Agent is a *reader and a
proposer*, never the owner. Chat is working memory; the DB is durable truth.

## Justified abstractions (and rejected ones)

**Justified (each protects a concrete future change):**

| Concept | Role | Protects against |
|---|---|---|
| **ProjectContext** | immutable, per-turn assembly of SELECTED project material handed to the Agent (a value object, freshly built per turn — NOT a DB connection) | the "agent sees the whole project / sees chat as truth" failure |
| **ContextSelection** | the declarative query describing what to include (project + selected object subtree + relation/evidence/decision filters + artifact REFERENCE list) | implicit prompt-stuffing; enables an auditable /context fetch contract |
| **ContextBuilder** | the read-only assembler that executes a ContextSelection against the domain read API and returns a ProjectContext; the only place truth becomes context | unbounded dumps; reference-not-embed; whole-project sends |
| **AgentSession** | ephemeral conversation container (prompt + ProjectContext + catalogs). Explicitly not persistent, not truth | chat becoming durable truth |
| **ToolCatalog / SkillCatalog** | thin mappers: typed, capability-derived tool calls; task → which skills load (never a content DB) | raw agent writes; always-on skill encyclopedia |

**Explicitly rejected** (overengineering): no AgentMemory DB wrapper, no RAG/
semantic-index pipeline, no generic AgentGateway, no always-on skill encyclopedia.

## What enters context automatically vs explicitly

- **Automatic (small, structural, safe):** project identity/name + membership
  context; the scientific-object *tree skeleton* (IDs + labels + types + parent
  links only, no metadata blobs); the active object subtree (a bounded
  ContextSelection); **reference headers** for large artifacts (id, type, provider,
  external id, checksum, size, content-type, version, originating run) — **never
  artifact bytes**; the few skills for the task.
- **Explicit (only when the Agent asks or the user does):** full metadata of a
  specific object; the *content* of a large artifact via an `inspect_artifact`
  tool call through the owning provider driver (returns a bounded preview, not a
  copy into Core); evidence/decision graphs beyond the active subtree; provider
  capability discovery; any other project's material.

**Avoiding whole-project dumps:** ProjectContext is a ContextSelection, not "the
project." Send the skeleton, not leaves; reference headers, not content; cap the
context budget and truncate; anything not selected is fetched on demand via a typed
tool.

## Representing objects / evidence / decisions to the Agent

As **typed, addressable references**, not prose dumps:

```text
ObjectRef   {type, id, authority?, native_id?, label}
EvidenceRef {id, kind, authority, native_id, role/polarity}
DecisionRef {id, status, superseded_by? (derived reverse of the `supersedes` edge),
            evidence_ids[]}
```

(External identity is `(authority, native_id)`, never the access provider — see
`PROVIDER_CAPABILITIES.md`.)

The Agent reasons over IDs and addresses its own proposed writes back to the same
IDs. Tool schemas derive from the OpenAPI/domain schema; skills point at the schema
rather than copying it (no duplicated enums anywhere).

## The agent write path: typed domain operation, then promotion for commitment

**Two distinct gates — do not conflate them** (reviewer finding #10):

1. **Typed domain-operation gate — applies to ALL persistence.** An Agent (or any
   caller) can never emit a raw write. Every durable change is a typed domain command
   (`create_object`, `attach_evidence`, `record_decision` as draft, `link_relation`,
   ...) that passes through domain validation. This gate is the *generic* protection.

2. **Promotion gate — applies ONLY to committing a knowledge assertion.** Promotion is
   specifically the `Decision draft → committed` transition (ADR-0011): accepting an
   agent's proposed conclusion as project truth. Ordinary object/evidence creation is a
   typed domain operation, NOT "promotion."

```text
read context               (ContextBuilder executes ContextSelection → immutable ProjectContext)
→ reason                   (Agent thinks over ProjectContext + loaded skills)
→ propose action           (Agent emits a TYPED tool call / proposal — no raw DB edit)
→ typed tool call          (ToolCatalog-derived)
→ domain validation        (domain service validates ownership, project-belonging, authority)
→ persisted project truth  (ONLY validated domain writes land; a proposed Decision lands as draft)
→ (if committing a Decision) promotion gate → committed
```

**Hard rule:** an Agent can **never** emit an UPDATE/SQL/raw-object-write. Every
durable change is a typed domain command that passes through domain validation.
Unvalidated prose is never persisted as truth. **Promotion is required only to move a
Decision from draft to committed** — not for creating
an object or attaching evidence, which are typed domain operations (automatic or
policy-gated per the authority matrix).

**What needs human approval** — see the authority matrix in *Safety & authority*.

---

## Skills architecture

**Location invariant:** single canonical root `.agents/skills/` — no second
`skills/` tree.

**Principle:**
> Skill teaches judgment and procedure; typed APIs own executable truth.

| Category | Skills | Canonical source | Handwritten vs generated | Freshness |
|---|---|---|---|---|
| **Constitutional** (how to develop REvoLab) | project-architecture, engineering-workflow, architecture-review, security-boundaries | CLAUDE.md + docs/architecture + ADRs | handwritten, rarely changes | review against invariants |
| **Domain** (what REvoLab means) | scientific-object-model, provenance-lineage, project-context, decision-record, artifact-inspection | backend schema + OpenAPI | written; POINT at schema, never copy | drift-checked against generated schema |
| **Integration** (how to use an external capability) | revocompute, revodesign, openbio | the driver's capability contract + OpenAPI | procedural + generated capability/param reference | drift-checked against generated schema |

**Decision on which skills to add/revise/keep** (from the review):
- **KEEP:** engineering-workflow, decision-record, artifact-inspection,
  driver-development, project-plugin-development.
- **REVISE:** project-architecture (add promotion/consumer guidance),
  scientific-object-model (typed-addressability, version-vs-mutate),
  provenance-lineage (harden reference semantics), project-context (make it the real
  agent-context skill teaching ContextSelection + promotion).
- **ADD:** `security-boundaries` (constitutional, procedural) and
  `architecture-review` (constitutional, near-empty procedural). The integration
  skills (**revocompute / revodesign / openbio**) are **staged and MUST NOT be
  pre-created** — they exist only when the provider actually exists (creating them
  now is a premature-plugin failure).

The **SkillCatalog** resolves *task → which skills load*; it is not a database of
skill content.

---

## Safety & authority model

No approval infrastructure is built now — only the ownership/authority **model**.
Six operation classes, each with an autonomy level:

| Operation class | Agent autonomy | Gate |
|---|---|---|
| **Agent proposal** (emit typed tool call, draft a Note/Decision, reason) | Automatic | none — conversation only, never persisted truth by itself |
| **Domain mutation** (create/update a Note draft, attach evidence, create an object, **record a Decision as a draft**, link a Relation) | Automatic OR project-policy | typed domain command + domain validation (ownership, project-belonging, schema) |
| **Knowledge commitment (promotion)** — `Decision draft → committed` | Approval (authorized actor) | the promotion gate (ADR-0011); a distinct, higher-authority operation — never automatic for an Agent |
| **External compute submission** (submit an expensive REvoCompute job) | Explicit tool action / policy | must name task + cost/capability; can require approval above a threshold; fails closed on uncertainty |
| **External data write** (write to provider/artifact store) | Highly restricted | explicit agreement that the write is intended; never silent |
| **Project sharing change** (membership, role, making something public) | Approval | always human-approved |
| **Credential operation** (read/create/rotate credentials) | **NEVER exposed to agent** | totally denied, domain/operators only |

Default posture: only safe reads, proposals, and low-risk domain mutations are
automatic; anything touching money/compute, destructive ops, sharing, or credentials
trends to approval or denied — and approval **fails closed** on uncertainty.

Recorded in **ADR-0013** (authority) and the existing **ADR-0007** (skills).
