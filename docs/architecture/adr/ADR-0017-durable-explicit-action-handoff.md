# ADR-0017: Durable Explicit-Action Handoff — Persist Intent, Never Authority

> **Status: Proposed — pending human acceptance.**
> Accepted only by explicit human acceptance; a green test suite does not promote
> this ADR's status.

## Context

Phases 8–10 left a deliberate authority gap. The bounded Agent loop classifies a
Tool as `explicit_action`, refuses to execute it, and returns an ephemeral
`PendingActionRead` in the turn response. That proposal dies with the HTTP response:
it cannot survive a page reload, a conversation reload, or later human review, and
there is no durable record that a human can act on.

The accepted architecture already fixes the surrounding boundaries
(`ADR-0012` provider/capability, `ADR-0013` agent-as-consumer,
`PROJECT_TOOL_HARNESS.md`, `PROJECT_AGENT_RUNTIME.md`, `PROJECT_CONVERSATIONS.md`):

- the Agent is a consumer, never an owner: it may propose, never authorize;
- `explicit_action` means "propose only; a human authorizes";
- persistence is always a typed domain command; provider execution truth stays
  with the provider and is only ever referenced;
- credentials are owned by the credential store and materialize only at the
  last-mile execution boundary.

What was not yet decided is the **durable representation of a proposal** and how a
human authorization can execute it safely after arbitrary time has passed.

## Decision

### Action Request is a durable operational-intent row, not a Core domain

A new Project-and-Actor-scoped `ActionRequest` row records: identity, Project,
owning Actor, originating Conversation, canonical `tool_id`, the complete
schema-validated size-bounded argument payload, lifecycle status, bounded status
reason, timestamps, and the canonical result reference when one exists.

It is **not** a ScientificObject, Evidence, Decision, `ProjectResourceLink`,
`GlobalResourceRegistry` entry, conversation message, Agent memory, provider
execution truth, or a `ToolResult`. It never enters the scientific graph and is not
addressable by a global resource UUID.

Ownership follows the Phase-9 conversation lens: the proposing Actor in one Project.
Another Project member cannot inspect, execute, or reject it, and a guessed UUID is
not an existence oracle (readable membership is checked before the row is looked
up).

Action Handoff therefore lives in the Agent Context / application-orchestration
layer, and the accepted nine-domain Core DAG is unchanged. No new Core domain is
invented because a new table exists.

### Persist intent, never authority

The row stores no authorization decision, membership result, provider-health
snapshot, capability-availability projection, credential material, `secret_ref`,
`CredentialLease`, raw provider/model payload, system prompt, skill body, or hidden
reasoning. Proposal-time autonomy/execution/side-effect classes are stored for
presentation only and are never consulted as authority.

The full executable argument payload is persisted or the proposal fails closed; a
truncated display preview is not executable state. Arguments never reach the
conversation transcript, which keeps only inert bounded summaries.

### Execution re-derives authority from current truth

Execution is an explicit human/API operation (`X-Actor-Id` is a development scoping
seam, not authentication). Before any side effect it rebuilds: current readable
membership and Project state, current mutation-capable role, current ToolCatalog
(existence, autonomy, execution class, provider/capability resolution), the current
canonical input model revalidated against the stored arguments, current resource
visibility for every referenced resource, current provider availability and
credential presence, and current project policy.

Membership revocation, role change, Project tombstone, resource unlink, credential
removal, provider loss, Tool removal or autonomy change, and input-schema change all
take effect immediately. "Authorized when proposed" is never trusted.

### One-shot concurrency, durable claim

Two simultaneous human clicks must not submit twice. Execution first takes a
**durable** one-shot claim as an atomic conditional state transition
(`pending -> executing`); a claim that does not win fails closed. PostgreSQL's row
lock orders the transition across processes; the conditional update is the
cross-backend backstop. An in-memory flag is never production truth. Once terminal,
an action never silently executes again; a second desired attempt is a new Action
Request.

### Honest ambiguity for external side effects

A SQL transaction cannot roll back a successful REvoCompute submission, and the
REvoCompute submit contract exposes no client-usable idempotency key, so REvoLab
does not invent one. Failures after the request may have been sent (transport
failure, provider 5xx/gateway response, unexpected payload, or a 2xx without a task
identity) are recorded as `ambiguous` and are **never** automatically retried.
Explicit 4xx rejections and pre-side-effect local provider checks are `failed`
(no side effect occurred).

After a confirmed handle, the action marks `succeeded` and retains only the stable
reference to the canonical `RunReference` (created by the existing Phase-4
`record_compute_run` path, with its `consumed_as_input_by` edges). Action Request
never duplicates scheduler/task/Runner/artifact-publication state. A confirmed
handle whose canonical recording fails is retried once through the same
get-or-create path; only a second failure settles `ambiguous`, retaining the bounded
provider identity for human reconciliation. Failure paths roll back before writing
the terminal outcome, and the terminal write tolerates a failed transaction — a
durably claimed action can never be stranded in `executing` after a confirmed
external side effect.

### One canonical execution path per execution class

```text
local  explicit action -> the SAME closed LocalToolRuntime the human workspace
                          uses (decision.commit)
remote explicit action -> the SAME capability path the human compute endpoint
                          uses (services.compute_submit_handle +
                          record_compute_run; `compute_submit` wraps the same two
                          primitives for the human endpoint)
```

No second compute-submission implementation and no second Decision-promotion
implementation is introduced. Execute/reject are **not** Tools and never appear in
the Agent ToolCatalog: the model cannot approve its own action.

## Consequences

- The authority matrix is unchanged: `automatic` / `policy` / `explicit_action`,
  with `never_agent` operations still absent from the Agent ToolCatalog. No fourth
  autonomy class, no generic approval workflow, no background worker, no retry
  scheduler, no OIDC/RBAC.
- A proposal survives reload and later review; the same human sees the exact
  proposed operation and explicitly executes or rejects it.
- The remote compute vertical slice is real end-to-end: Agent proposal -> durable
  Action Request -> human execute -> current-truth revalidation -> canonical
  REvoCompute submission -> canonical `RunReference` observable in the existing
  Runs & Artifacts surface.
- Residual limitation accepted and documented: a process hard-killed after the
  durable claim and before the outcome write leaves the action in `executing`
  (in-progress, never auto-retried); it is represented honestly rather than being
  silently converted.
- Normative detail lives in `docs/architecture/AGENT_ACTION_HANDOFF.md`; consuming
  documents point at it.
