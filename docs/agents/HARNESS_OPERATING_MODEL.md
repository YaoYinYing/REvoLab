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
| B. Domain | `scientific-object-model`, `provenance-lineage`, `decision-record`, `project-context`, `artifact-inspection` | What things in the REvoLab world mean; evolves with the product |
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
package install, tests, build, lint
local postgres, localhost API
```

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
7. RALPH AUDIT        fresh reviewer → clean? no → fix + retry; yes → COMPLETE
```

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
