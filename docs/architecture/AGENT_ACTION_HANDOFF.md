# Agent Action Handoff — Durable Explicit-Action Requests & Human-Authorized Execution

> **Status: Accepted** (Phase 11; human-accepted during PR #12 review; squash-merged
> to `main` as commit `83f1827`).
> This document is normative for the Action Handoff sub-boundary. It does not
> introduce a tenth Core domain; Action Handoff is an **Agent Context /
> Application-orchestration sub-boundary** (the same placement family as
> `PROJECT_CONVERSATIONS.md`) and the nine-domain DAG is unchanged.

Related: `PROJECT_AGENT_RUNTIME.md`, `PROJECT_CONVERSATIONS.md`,
`PROJECT_TOOL_HARNESS.md`, `PROVIDER_CAPABILITIES.md`, `SYSTEM_ARCHITECTURE.md`,
`DOMAIN_BOUNDARIES.md`, ADR-0012, ADR-0013, ADR-0016, ADR-0017.

---

## 1. The invariant

> **Persist intent, never authority. Re-derive authority at execution time.**

An Agent may **propose** an explicit action. A durable Action Request records that
proposal so it survives a page reload, a conversation reload, and later human
review. An Action Request is **never** permission to execute:

- it stores no authorization decision, no membership result, no provider-health
  snapshot, no credential material, and no reference to credential material;
- `arguments_digest` is an UNKEYED content digest for corruption/out-of-band-edit
  detection, never an authentication tag and never authority;
- it is re-validated against **current** truth at execution time;
- the Agent can never read, execute, or reject it through a tool.

The final behavioural contract:

```text
The Agent proposes intent.
The human authorizes an action.
The system re-derives authority from current truth and executes
through the canonical Tool / domain / provider path.
```

## 2. What an Action Request is (and is not)

An Action Request is **durable operational intent**: "this Actor, in this Project,
proposed this canonical Tool with this validated bounded input at this time, from
this conversation".

It is **not**:

```text
ScientificObject        Evidence            Decision
ProjectResourceLink     GlobalResourceRegistry entry
Agent memory            conversation message
provider execution truth
```

It is not a `ToolResult`, not a `RunReference`, and not a scheduler/job record. It
never enters the scientific graph, never carries provenance semantics, and is not
addressable by a global resource UUID.

Ownership: `Project` + owning `Actor` (the Actor whose turn produced the proposal),
exactly like a private conversation. An Action Request may be attached to the
originating `ProjectConversation`, but the link is descriptive, not an authority
grant.

## 3. Ownership and privacy

| Question | Answer |
|---|---|
| Who owns an Action Request? | The proposing Actor, scoped to one Project. |
| Who can see it? | Only that Actor, and only while the Project is readable/active. |
| Who can execute/reject it? | Only that same Actor, through an explicit human/API request. |
| Can another Project member see it? | No. Actor B gets 404 for Actor A's action, even in one Project. |
| Is a guessed UUID an existence oracle? | No. Readable membership is checked first (403); the lookup then matches `(id, project_id, actor_id)` and fails 404. |
| Shared/team/multi-party approval? | Out of scope. There is no approval queue. |

## 4. What it persists — and what it must never persist

Persisted:

```text
action identity (UUID)
project_id, owning actor_id, optional conversation_id
canonical tool_id
the complete, schema-validated, size-bounded argument payload
lifecycle status + bounded, sanitized status reason
created/updated/claimed/resolved timestamps
the canonical result reference when one exists (RunReference resource id,
or the Decision id for the local commit action)
proposal-time autonomy / execution_class / side_effect_class (presentation only,
NEVER consulted as authority)
```

Never persisted:

```text
credentials, CredentialLease, secret_ref, authorization headers, provider tokens
raw provider/model request or response
system prompt, trusted skill body, hidden reasoning
membership/authorization result, provider-health snapshot, capability availability
a ToolResult payload kept "for convenience"
scheduler/task/Runner/artifact-publication state owned by REvoCompute
```

The complete executable argument payload must be present and schema-valid. A
truncated display preview is **not** executable state: the durable row stores the
full validated payload or the proposal fails closed. Argument size is bounded
explicitly (the persistence boundary enforces its own bound, not only the Agent
loop); an over-bound validated action is refused instead of being stored partially. Conversation transcript rows continue to hold only inert summaries —
arguments live only in the Action Request persistence boundary.

## 5. Proposal path

```text
model tool call
    -> canonical ToolCatalog lookup (unknown / unavailable fails closed)
    -> current autonomy + execution_class + side_effect_class classification
    -> typed schema validation against the canonical input model
    -> explicit_action classified: bounded, non-secret canonical payload
    -> durable Action Request (pending) in the same transaction as the turn
    -> bounded PendingAction surfaced to the model + human (display only)
```

Proposal is not authorization. Nothing about the proposal is treated as a
decision later.

## 6. Execution path

```text
explicit human/API request (X-Actor-Id is a development scoping seam, not auth)
    -> current readable membership + active Project (else 403, no oracle)
    -> the Action Request must be pending AND owned by this Actor (else 404/409)
    -> preflight against CURRENT truth (no side effect yet):
         current ToolCatalog: tool still exists, still explicit_action /
         external-action; provider_key / capability_kind still resolve
         current canonical input model revalidates the stored arguments
         current membership role still mutation-capable where required
         current resource visibility for every referenced resource
         current provider availability + current credential presence
         current project policy
    -> durable one-shot claim (pending -> executing) as an atomic conditional
       state transition; a claim that does not win fails closed
    -> canonical execution
         local  explicit action -> the SAME closed LocalToolRuntime the human
                                   workspace uses (e.g. decision.commit)
         remote explicit action -> the SAME capability path the human compute
                                   endpoint uses (services.compute_submit_handle
                                   + record_compute_run)
    -> post-execution truth
         success      -> create/reuse the canonical RunReference (+ provenance
                         edges) and mark succeeded with the result reference
         definite     -> mark failed (no external side effect took place)
         uncertain    -> mark ambiguous (see §8)
```

### The authorization surface must describe the action that will actually run

A remote explicit action carries provider identity twice: in the canonical
`tool_id` (the authority execution resolves through the live ToolCatalog) and in
the canonical input model's `provider_key` field (what a human reads before
authorizing). The two MUST agree. A payload naming a different provider is refused
at proposal (fail closed, no durable row) and re-refused at execution (defense in
depth for any row written out of band), because otherwise a human would authorize
the operation under a false description of the external side effect. A local
explicit action has no provider identity and is exempt.

### State-machine invariant

A `succeeded` action always names the canonical result it produced. This is
enforced by the durable schema itself (`ck_action_succeeded_has_result`), not only
by application code: a `succeeded` row with no `result_run_id`/`result_decision_id`
is not representable.

Execution-time revalidation is not an optimization: membership revocation, role
change, Project tombstone, resource unlink, credential removal, provider loss,
Tool removal, autonomy change, or an input-schema change must all take effect
immediately. "It was authorized when proposed" is never trusted.

## 7. Lifecycle

```text
pending    proposed; awaiting an explicit human decision          (non-terminal)
executing  a human execution claimed it; one-shot, single writer  (non-terminal)
succeeded  canonical result reference recorded                    (terminal)
failed     definite failure with no external side effect          (terminal)
ambiguous  external outcome uncertain; never auto-retried         (terminal)
rejected   human rejected it; no side effect                      (terminal)
```

Terminal states are one-way: a terminal action can never silently execute again.
A rejected action is terminal; a second desired attempt is a **new** Action
Request. `executing` is a durable claim, not a lock in process memory: on
PostgreSQL the row transition is the ordering primitive, and the atomic
conditional `UPDATE` is the cross-backend backstop.

## 8. External side effects and ambiguity

Database writes and remote side effects are different consistency domains. A SQL
transaction cannot roll back a successful REvoCompute submission.

The REvoCompute submit contract exposes **no client-usable idempotency key**, so
REvoLab does not invent one. Failure after the request may have been sent is
classified from the provider boundary:

```text
explicit 4xx rejection received (auth / invalid_param / not_found)
        or a pre-side-effect local provider check (driver not READY, health,
        missing credential)
    -> definite failure; no side effect; safe to fail the action
transport failure, provider 5xx/role/timeout gateway response, an unexpected
upstream payload, or a 2xx that carried no task identity
    -> ambiguous: the submission may have succeeded
```

An ambiguous action is **never** automatically retried and the external submission
is never repeated by the system. (The LOCAL canonical recording may be retried once
— that is not a provider call and cannot duplicate the side effect.) Its durable
state is the honest answer; recovery is an explicit human decision (a new Action
Request, or reconciliation with the provider). Exactly-once limitations are never
hidden behind retries.

After a confirmed handle, the canonical RunReference (identity card) and the
`consumed_as_input_by` provenance edges are created through the **existing**
`record_compute_run` path. Action Request stores only that stable reference — never
REvoCompute's mutable run state, artifacts publication state or scheduler state.

If the canonical recording fails after a confirmed handle, the failure path rolls
back and then RECOVERS the canonical truth on a healthy transaction:

1. the recording is retried once through the same get-or-create path, so a
   partially committed attempt is COMPLETED (missing links/edges) rather than
   duplicated (the external submission is never repeated);
2. if that also fails, the canonical RunReference is read back by the confirmed
   provider identity — the failed attempt commits the identity card before its
   provenance edges, so it may already exist. The read-back RE-APPLIES the canonical
   immutable identity contract (`provenance.assert_reference_compatible`), never
   accepting a row merely because it shares `(authority, native_id)`. A **compatible**
   reference settles the action `succeeded` with the REAL reference plus a bounded
   note that its input provenance could not be completed — it is never reported as
   "nothing was recorded" when something was. An **incompatible** existing reference
   (at minimum a different `task_type`) is NOT this action's result: it is never
   attached, and the action settles `ambiguous` with bounded reconciliation detail.

Only if no canonical identity exists does the action settle `ambiguous`, and then the
bounded `status_reason` retains the confirmed provider identity
(`authority/native_id`) so a human can reconcile. That is the ONE place an external
identity appears outside a RunReference — deliberately, because the canonical card
could not be created — and it is not authority and not copied mutable state.

Failure paths roll back before writing the terminal outcome, and the terminal write
itself tolerates a session left in a failed transaction (the one-shot claim is
already durably committed, so leaving the row `executing` after a confirmed external
side effect would be a lie). The canonical commands this path reuses own their own
commit (`commit_decision_row`, `_persist_run_reference_trusted`), so a SAVEPOINT
cannot wrap them; the rollback-first discipline is what prevents an uncommitted
partial local write from being persisted by the terminal write.

Nothing raised inside the recovery may escape it: a compatibility conflict, a
read-back failure and a retry failure all resolve to a bounded outcome, so the action
is always settled terminally rather than stranded in `executing`. A terminal outcome
that cannot be written at all (the DB rejects it twice) and a read-back of the
settled row that fails after the outcome was committed both surface as an explicit
error while the durable state stays whatever was actually committed. The system never
converts such a case into a success, and never auto-retries the external call.

## 9. Authoritative surfaces

Human/API operations (never exposed to the Agent ToolCatalog):

```text
GET  /api/projects/{project_id}/agent/conversations/{conversation_id}/action-requests
GET  /api/projects/{project_id}/action-requests/{action_request_id}
POST /api/projects/{project_id}/action-requests/{action_request_id}/execute
POST /api/projects/{project_id}/action-requests/{action_request_id}/reject
```

`execute`/`reject` are **not** Tools and never appear in `GET
/projects/{id}/tools` or `GET /projects/{id}/agent/tools`. The model cannot approve
its own action. No auto-approval rule, retry scheduler, notification, background
worker or workflow engine exists.

## 10. Authority matrix — unchanged

```text
automatic       Agent may execute (safe reads)
policy          Agent may execute; typed domain mutation gated by project policy
explicit_action Agent may PROPOSE only; a human authorizes execution
never_agent     absent from the Agent ToolCatalog entirely
```

Phase 11 adds no fourth autonomy class and weakens no existing
prompt-injection/authority regression. Persisted hostile conversation, Note or
project text may not auto-authorize an action, flip `explicit_action` to
`automatic`, call execute/reject, widen resource visibility, select another Actor,
supply credentials, or bypass current provider availability — the Action Request is
derived from a typed model tool call, never from parsing natural-language text.

## 11. Frontend surface

The Action Handoff UI lives inside the existing Agent conversation surface and is
bounded to the current Actor × Project × conversation scope:

- it shows the proposed Tool, local vs remote execution, side-effect class, the
  canonical bounded arguments in human-readable form, the current state, and what
  Execute will do;
- for a compute submission it shows provider, task kind, input resource identities
  and parameters (never credential details);
- it provides Execute / Reject only for a `pending` action, only for a
  mutation-capable membership;
- it never executes on render, reload, navigation or model response;
- a reload preserves a pending Action Request, and a Project/conversation switch
  discards stale in-flight responses (the same scope guards as Phases 8–10);
- after success it surfaces the resulting Run reference, which the existing
  Runs & Artifacts surface already observes.

## 12. Non-goals

```text
generic approval workflow      multi-party approval       organization policy engine
background action worker       scheduled actions          retry scheduler
workflow/DAG execution         recursive/background Agent Agent self-approval
auto-approval rules            notifications              OIDC / RBAC framework
public sharing                 RAG/vector memory          semantic Agent memory
autonomous remote-provider Agent execution
```

Phase 11 is exactly one explicit handoff boundary.

> **Phase-13 note.** The `autonomous remote-provider Agent execution` non-goal above
> means the Agent never autonomously performs an external **action** (a submit, a
> mutation, anything requiring authorization or having an external side effect);
> every such call still becomes a durable Action Request or stays on the human
> capability endpoints. It does NOT forbid a bounded remote READ-ONLY read: Phase 13
> lets the loop execute exactly the registered remote read-only reads
> (`revolab.tools.remote_reads`), which persist nothing and need no ACTION-REQUEST
> authorization (they still run the ordinary current Project read authorization:
> readable membership, active Project, and project policy).
> See `EXTERNAL_LITERATURE_DISCOVERY.md` (ADR-0019).

## 13. Accepted limitations

- A process hard-killed after the durable claim and before the outcome write leaves
  the action in `executing`. This is reported honestly as an in-progress claim with
  its claim time; the system never auto-retries it, and a further execute request
  fails closed. It is not silently converted into success or failure. (A failure the
  process DOES observe is settled terminally or surfaced as an explicit error while
  the durable state stays whatever was actually committed — see §8.)
- If the provider accepted a submission but the local canonical RunReference could
  not be recorded, the action is marked `ambiguous` rather than pretending either
  outcome.
- `X-Actor-Id` remains a development scoping seam; real authentication/OIDC stays
  deferred (ADR-0008/0011).
