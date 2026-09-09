# REvoLab Harness Operating Model

This document is the top-level control-plane design for how DeepSeek Harness is
used to develop REvoLab. It defines *who writes code, who decides completion,
what may loop autonomously, and what must stop and wait for a human*.

`CLAUDE.md` holds only the short invariants derived from this model. This file is
the durable truth for the harness; `CLAUDE.md` is the load-bearing summary.

---

## 1. The four loops

There are **four distinct loops** in the picture. They must not be conflated.

### Loop 1 — Agent Loop (built-in, not managed)

```text
model → tool call → tool result → model → ...
```

This is the innermost execution loop that DSH core provides (session log →
prompt assembly → LLM → tools → append results). It answers *"how is work done
inside one round"*. We do not manage it; it is not *"how a whole feature is
developed over hours"*.

### Loop 2 — Goal Loop (REvoLab's main development loop)

```text
/goal → Round 1 (plan → inspect → subagents → implement → test)
      → still incomplete → Round 2 (inspect → fix → test)
      → ...
```

`/goal` is a durable objective that the goal-round-driver continues in the same
session, preserving context. It is the normal way to develop:

```text
Human → one well-defined /goal → Primary Integrator → many rounds
```

instead of a human repeatedly prompting for continuation.

**A Goal is a completion contract, not a TODO list.** Describe:

```text
Objective
Scope
Acceptance
Non-goals
Stop conditions
```

DSH states Goal has **no independent evaluator** and a round cap is not a
token/time/cost budget. Therefore `"the model says done"` is not meaningful;
completion is **Acceptance Gates == PASS** (executable machine evidence).

### The runaway guard: every autonomous Goal is bounded

Autonomous Goal loops are not open-ended. A Goal may run unattended **only**
within a finite, pre-committed envelope. Every Goal launched via `/goal` for
substantial multi-round work must carry all of the following before it starts:

```text
FINITE ROUND BUDGET       a concrete maximum number of rounds; the loop halts
                          when it is exhausted, even if Acceptance is not yet PASS

ACCEPTANCE GATES          the objective plus one or more concrete, executable
                          acceptance checks (machine gates, not model prose);
                          completion is gates == PASS, never model self-assessment

NON-GOALS                 what the agent must NOT do, and when to stop instead of
                          "helpfully" broadening scope

NO-PROGRESS DETECTOR      a rule for recognizing that a round produced no forward
                          movement (no gate moved closer, no durable change) so the
                          loop does not spin forever

PERMISSION CEILING        a fixed maximum scope/permission the Goal may use, granted
                          up front and NEVER self-escalated by the agent
```

The **permission ceiling** is fixed when the Goal is declared and is not a floor:
the agent may operate up to it, but may never ask to raise it and may never widen
it on its own. Scope expansion and permission expansion are both **human-only**
decisions.

The **no-progress detector** plus the acceptance gates define concrete
**STOP-AND-ASK-HUMAN** triggers. At any of the following the agent must stop
and ask the human rather than push forward:

```text
(a) two consecutive rounds with no progress     (no-progress detector trips)
(b) the same acceptance gate failing repeatedly (it is not converging; keep
    retrying a broken gate is not productive)
(c) a request to expand scope                   (goes beyond the declared Non-goals)
(d) a request to expand permissions             (exceeds the Permission ceiling)
(e) a CONSTITUTIONAL architecture change — a decision that changes accepted domain
    ownership, an accepted ADR/invariant, the product boundary, or the safety/approval
    authority. (An implementation-architecture decision WITHIN already-accepted
    ADRs/invariants is **not** a stop trigger — that is the Primary Integrator's job.)
```

When a trigger fires, the agent stops and reports to the human with the round
budget consumed, the gates tripped, and the concrete decision required — it does
not keep looping, self-escalate, or reinterpret its acceptance to declare
victory.

### Loop 3 — Subagent / Workflow Loop (parallel thinking inside one round)

Subagents are an official capability (fresh in-process, fork, Codex, Claude
Code, DSH SDK, ...); the parent can list/send/interrupt. Workflow lets the
Primary write an orchestration script that starts many subagents in bounded
fan-out/fan-in.

A complex round should be:

```text
Primary Integrator
  ├── subagent A  architecture ownership audit
  ├── subagent B  backend/domain inspection
  ├── subagent C  frontend/OpenAPI inspection
  └── subagent D  tests/CI/migrations inspection
        → reports only → Primary reconciles, decides, writes one patch, runs gates
```

**Subagents are investigators, not committee members with shared write access.**

- Default: Primary writes; subagents read/analyze/test/report.
- Parallel child *writes* are allowed only when ownership is completely disjoint
  (e.g. one child owns `docs/**`, another owns `frontend/**`).
- Do **not** split one shared domain contract across parallel writers (e.g. A
  edits `models.py`, B edits `schemas.py`, C edits `api.py`): three locally
  correct but globally inconsistent patches result.

### The subagent composition contract

Every delegated subagent is dispatched under an explicit composition contract.
A bare "go investigate / go fix this" prompt is not enough; the delegation must
declare each field before the subagent starts:

```text
Role                what the subagent is (investigator, specialist, reviewer) and
                    the one thing it is responsible for

Question            the concrete question / deliverable it must answer

Allowed paths       the explicit file / workspace scope it may touch — and the
                    explicit list it must NOT touch

Write permission    whether the subagent may write at all (default: NO — subagents
                    read/analyze/report, never write the integration)

Evidence required   what must be cited / attached for the result to count
                    (machine gate output, file paths, test results — not prose)

Output schema       the structured shape of a completed result the parent expects
                    back (so the parent can reconcile, not free-form prose)

Stop condition      when the subagent is done and may not go further (a bounded
                    scope, a bounded round count, or a STOP-AND-ASK-HUMAN trigger
                    from the runaway guard)
```

The contract reflects and reinforces the **"Primary Integrator owns writes;
subagents read/analyze/report"** model:

- **NO RECURSIVE SUBAGENT SPAWNING BY DEFAULT.** A subagent must not spawn its
  own subagents unless that permission is explicitly enabled in its contract.
  Delegation is a Primary-integrator responsibility, kept one level deep by
  default.
- **BOUNDED CONCURRENCY.** The parent starts a bounded, pre-declared number of
  parallel subagents and reconciles their reports itself; it does not let fan-out
  grow without limit.
- **CHILD AUTHORITY ≤ PARENT AUTHORITY is policy, and must be *enforced by the
  selected subagent provider* — not assumed.** "Child permissions never exceed the
  parent's" is an intention, not an automatic property of every subagent runtime. A
  reuse of the parent's conversation seed does not by itself inherit the parent's
  sandbox/tool/authority; different providers (in-process DSH child, Codex, Claude
  Code) map DSH's grant to different native mechanisms. So every delegation carries an
  explicit grant, and the parent may use a provider only when that provider can
  **provably map** the grant to its own permission profile:

  ```text
  SubagentGrant {
      provider
      cwd
      allowed_paths
      tool_allowlist
      sandbox_mode            (e.g. workspace-write)
      approval_policy
      max_depth
      max_children
  }
  ```

  Enforcement rule: **policy intent ≠ enforcement boundary.** For the in-process DSH
  child, use its scope mechanism; for Codex / Claude Code, map each field to that
  provider's native permission profile. A provider that cannot prove/map the grant runs
  **read-only + tool-denied**, or is **not used** for that delegation. There is no
  transitive escalation through the delegation tree.

### Loop 4 — Ralph (final independent convergence / certification loop)

Ralph is **only** for explicitly requested fresh-agent iterative convergence
*after a coherent implementation exists*. Every round is a fresh child over the
shared workspace, with no previous-conversation seed.

```text
Goal development finished → tests pass → Ralph independent audit
  → fresh reviewer fixes issue → fresh reviewer finds another → fresh clean round → COMPLETE
```

It is not for starting a new feature (that loses design continuity). Ralph is
*given durable memory by the shared workspace*, not by inheriting chat.

---

## 2. The four-layer division of labor

```text
Goal       = construction loop
Subagents  = bounded specialist reasoning
Workflow   = bounded parallel orchestration
Ralph      = independent convergence / certification loop
```

---

## 3. The Primary Integrator

Integration is an explicit role, not an afterthought. The Primary agent owns:

```text
read goal
read CLAUDE
load relevant skills
inspect machine state
decompose
delegate independent questions
collect reports
resolve conflicts
make ONE architectural decision
implement coherent patch
validate
update machine truth
decide next round
```

Subagents **never vote on architecture**. When subagents return different
candidate designs, the Primary must not resolve by majority; it returns to:

```text
Who owns this knowledge?
What is the smallest durable abstraction?
What does the current vertical slice require?
```

**Two-layer architecture authority (round 5) — never conflate the two:**

```text
Implementation architecture decision
    within already-accepted ADRs/invariants (choose the smallest durable
    abstraction, the physical schema detail, a phase-slice decomposition)
        → the PRIMARY INTEGRATOR decides autonomously.

Constitutional architecture change
    changes domain ownership, an accepted ADR/invariant, the product boundary,
    or the safety/approval authority
        → HUMAN only (the STOP-AND-ASK-HUMAN trigger).
```

The runaway guard's trigger (e) therefore fires only for the **constitutional** layer —
exactly when the agent must not proceed on its own. The daily implementation forks are
what the Primary's intelligence is for.

---

## 4. Skills

### Location

Do **not** keep a second `skills/` tree as another source of truth. The fixed
project roots are:

```text
.agents/skills/
  ├── project-architecture/
  ├── architecture-review/
  ├── security-boundaries/
  ├── engineering-workflow/
  ├── scientific-object-model/
  ├── provenance-lineage/
  ├── decision-record/
  ├── project-context/
  ├── artifact-inspection/
  ├── project-tool-harness/
  └── driver-development/
```

### Role

Skills are **project-local operating knowledge loaded per task** — not agent
commands, not business logic, and not an always-on encyclopedia in the system
prompt.

- A skill points to canonical schema/OpenAPI rather than duplicating truth.
- Skills never copy the schema; they instruct "you MUST inspect the
  OpenAPI/domain schema before acting".
- The `engineering-workflow` skill is where substantial-task execution lives. It
  carries the ODDRIVC procedure and the **long-running refactor protocol**
  (three sources of truth, persistent checklist, migration-first deletion,
  vertical-slice migration, executable acceptance, and the DESIGN/EXECUTION/
  MACHINE definition of done). Long refactors follow that protocol, not the
  loose root-level file it replaced.

### Three categories

| Category | Examples | Meaning |
|----------|----------|---------|
| A. Constitutional | `project-architecture`, `architecture-review`, `security-boundaries`, `engineering-workflow` | How to develop REvoLab; almost never changes |
| B. Domain | `scientific-object-model`, `provenance-lineage`, `decision-record`, `project-context`, `artifact-inspection`, `project-tool-harness` | What things in the REvoLab world mean; evolves with the product |
| C. Integration | `revocompute`, `revodesign`, `openbio` | How to correctly use an external capability; exists only when the provider exists |

---

## 5. Project truth is the loop's external memory

Goal has session memory; Ralph has none; subagents may be fresh; compaction can
lose detail. Durable long-term memory is the repository:

```text
CLAUDE.md                  → architectural invariants
docs/architecture/         → ADR, domain model, ownership
IMPLEMENTATION_STATE.md    → actual machine state
tests/                     → executable acceptance
.agents/skills/            → operating knowledge
```

**Workspace is memory; conversation is working memory.** This is exactly why
Ralph uses the shared workspace as durable memory rather than inherited chat.

---

## 6. Safety plane

DSH is developer-preview and not production-safe; sandbox/approval reduce risk
but are not a sole security boundary. Never run an overnight
`danger-full-access` promise.

Default REvoLab permissions:

```text
workspace-write
+ approval = ask
```

`danger-full-access` (unrestricted sandbox + never approval) is a distinctly
more dangerous mode and is never the default.

### What the agent may do

```text
/repo/REvoLab/**                   read/write
package install (NORMAL class)     manifest/lockfile/build backend/install scripts
                                   unchanged from an already-approved baseline
tests, build, lint
localhost API                      (read/test the running app)
```

### Package install is split into two classes (reviewer finding #11, refined round 4)

`package install` is **not one homogeneous operation**. The earlier rule split on
"existing dep vs new dep", but that is misleading: `pip install -e '.[dev]'` on an
existing dependency still executes the declared PEP 517 build backend, and `npm ci` on
a locked tree still runs `install`/`postinstall` lifecycle scripts. "Locked" does not
mean "runs no code". The boundary is therefore **whether the dependency manifest — and
the install code it declares — matches an approved baseline**:

```text
(a) NORMAL — no approval needed:
    the dependency manifest, lockfile, build backend, and install/lifecycle scripts
    are UNCHANGED from an already-approved baseline, so the declared install path
    may execute, e.g. `pip install -e '.[dev]'` or `npm ci` against the approved
    manifest.

(b) REQUIRES APPROVAL:
    - the agent changed the manifest / lockfile / build backend / install scripts
      (adding, updating, or pinning dependencies; editing pyproject/npm scripts), or
    - the baseline itself has not yet been approved once by a human, or
    - installing an arbitrary / unreviewed package.
```

Once a human has approved a baseline manifest, running its declared install path is a
normal project operation; **any change to that manifest (or to the code the manifest
will execute) re-arms approval**, because install time is code-execution time, not just
file writing. Two families of install-time scripts are the exact code-execution risks
the baseline approval covers:

```text
npm lifecycle scripts    preinstall / install / postinstall
Python build backends    PEP 517 / PEP 518 build-system, setup.py / egg_info,
                         and Python post-install steps
```

**Machine identity of the approved baseline (future executable-policy note, P2 not
blocking PR1):** "approved baseline" must not stay a chat memory. The durable form is a
**digest of the manifest surface that can execute code**:

```text
approved_dependency_baseline
    = digest(pyproject + lockfile(s) + package scripts + build config)
```

The agent computes the same digest and may run the declared install path only while the
digests match. This belongs in Harness tooling (not Core project truth) and may be
deferred past PR1 merge.

### Container-engine operations are a privileged external capability (reviewer finding #8)

The Docker/Podman **daemon is outside the filesystem sandbox**. A process with daemon
access can create containers with host bind-mounts, `--privileged`, or host PID/IPC
flags and thereby **bypass `workspace-write` file restrictions**. Therefore:

- Container-engine access is **NOT an ordinary workspace write** — it is a privileged
  external capability that requires approval to invoke.
- The only sanctioned container use is the **known `docker-compose.yml` at the repo
  root** for `local postgres`. Because that file is itself editable by the agent, the
  **filename is not the security boundary** — the *effective* configuration is. Before
  any daemon invocation:

```text
docker compose config                 (render the EFFECTIVE merged config,
                                       not the checked-in file)
    ↓
machine/policy validation:
    no --privileged
    no host PID / IPC namespace sharing
    no docker.sock mount
    no bind-mount outside the approved workspace/data root
    ↓
approval
```

```text
hard rules (unchanged):
known project docker-compose file        (not an arbitrary image/container)
no --privileged
no host PID / IPC namespace sharing
no docker.sock mount into a container
no bind-mount outside the approved workspace/data root
approval required when invoking host Docker/Podman
```

> **Policy must inspect the effective Compose configuration, not trust the filename.**

- **Never treat a subagent or a sandbox as a safe proxy for Docker**: delegating a
  container command to a subagent does not isolate the daemon. See "Subagents are not
  a security boundary" below.

### What the agent must NOT do without explicit human authorization

```text
modify /repo/REvoCompute, /repo/REvoDesign
write /mnt/db, production data
git push, merge PR, release, production deploy
SSH remote mutation, cloud destructive operation, credentials inspection
```

### Subagents are not a security boundary

Workflow worker threads isolate execution from the host event loop but are
**not a security boundary**. Delegating dangerous commands to a subagent is not
safer; the sandbox policy is the execution-permission boundary, and the sandbox
cannot protect resources explicitly exposed to it.

### Approval fails closed

Approval outcomes are `allowed-once | rejected | cancelled | unavailable`; with
no answerer the result is `unavailable` and the consumer fails closed. Project
policy matches:

```text
uncertain permission → stop / ask        (never assume yes)
```

---

## 7. The seven-phase development workflow

```text
1. HUMAN OBJECTIVE    one /goal
2. ORIENT             CLAUDE, ADR, IMPLEMENTATION_STATE, relevant skills
3. INVESTIGATE        parallel fresh subagents (architecture/backend/frontend/CI)
4. INTEGRATE          primary agent decides the smallest coherent vertical patch
5. EXECUTE            write; tests; migrations; contracts; browser smoke
6. VERIFY             machine gates; update IMPLEMENTATION_STATE
                       └─ material defect? yes → next Goal round; no → 7
7. INDEPENDENT REVIEW fresh reviewer audits the patch
   ├─ normal fresh reviewer by default
   └─ Ralph ONLY on explicit human request (fresh-agent convergence loop;
      never a fixed mandatory step)
   → clean? no → fix + retry; yes → COMPLETE
```

This aligns the top-level control plane with the `engineering-workflow` skill:
**Ralph runs only when explicitly requested** and is never a mandatory fixed phase.
A plain fresh-subagent review is the default independent review.

The fixed orchestration algorithm — **ODDRIVC**:

```text
ORIENT
DECOMPOSE
DELEGATE
RECONCILE
IMPLEMENT
VERIFY
REVIEW
CONTINUE | BLOCK | COMPLETE
```

Never jump from ORIENT straight to IMPLEMENT.

---

## 8. Summary

```text
Goal gives persistence.
Skills give judgment.
Subagents give breadth.
Primary integration gives coherence.
Tests give truth.
Ralph gives independent convergence.
Sandbox gives containment.
Human gives authority.
```
