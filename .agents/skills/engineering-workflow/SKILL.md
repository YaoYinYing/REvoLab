---
name: engineering-workflow
version: 0.2.0
description: Run a substantial REvoLab task or long-running refactor through the harness operating model (ODDRIVC).
---

# Engineering Workflow

## When to use
Use for any substantial multi-step task (feature, refactor, cross-cutting change)
where a full /goal round, delegation, or independent review is warranted. For a
trivial single-step edit, skip this and act directly.
For large architectural refactors, migrations, or repository-wide redesigns that
cannot be completed reliably in one patch, also follow the
**Long-running refactor protocol** below.

## Canonical sources
Read `CLAUDE.md` for the short invariants and
`docs/agents/HARNESS_OPERATING_MODEL.md` for the full operating model. Read the
current `IMPLEMENTATION_STATE.md` before claiming anything about machine state.

---

## Procedure — follow ODDRIVC in order

Do not jump from ORIENT straight to IMPLEMENT.

1. **Orient** — read the goal, `CLAUDE.md`, relevant ADRs, `IMPLEMENTATION_STATE.md`,
   and load the applicable project skills (constitutional + domain).
2. **Decompose** — split the objective into independent questions and one
   coherent minimal patch. Identify what is shared (same domain contract) and
   must therefore stay in one writer.
3. **Delegate** — outsource bounded investigation to fresh subagents
   (architecture / backend / frontend / tests). Subagents *read, analyze,
   test, report*; they do not share write access by default.
4. **Reconcile** — collect reports and resolve contradictions. Make **one**
   architectural decision yourself; subagents never vote on architecture.
5. **Implement** — write the smallest coherent vertical patch in the workspace.
6. **Verify** — run the executable gates (pytest, Alembic drift, typecheck,
   frontend build/test). Completion requires machine acceptance, not
   self-assessment. Update `IMPLEMENTATION_STATE.md` to record actual state.
7. **Review** — if required, run an independent (Ralph / fresh) audit, then
   decide: CONTINUE (next Goal round), BLOCK (genuine external dependency), or
   COMPLETE (Acceptance Gates == PASS).

---

## Long-running refactor protocol

For refactors, migrations, and repository-wide redesigns keep **three separate
sources of truth** and never collapse them:

```text
DESIGN / TODO            → architectural truth
IMPLEMENTATION_STATE.md  → current execution/progress truth
tests / acceptance cmds  → machine-verifiable truth
```

### Rules

1. **Read the design before editing.** Read the active design doc (`TODO.md`,
   `DESIGN.md`, `MIGRATION.md`, `RFC.md`) completely. Do not infer completion
   from the existence of classes/modules/tests; a refactor is complete only when
   the intended dependency direction and ownership model are actually in use.
2. **Keep a persistent execution checklist.** Translate every `MUST`, migration
   step, acceptance criterion, and invariant into checklist items tracked in
   `IMPLEMENTATION_STATE.md`; it is live execution state, not documentation.
3. **Keep progress durable.** After each milestone record: completed items,
   current phase, migrated files, verification performed, known failures,
   remaining blockers, next action — enough for another session to resume
   without reconstructing from git history.
4. **Recover on context loss.** After compaction, new session, or long
   interruption, re-read repo instructions, the design doc, and
   `IMPLEMENTATION_STATE.md`; never rely solely on conversation memory.
5. **Architecture ownership outranks minimum diff.** For ordinary bugs prefer
   small focused changes; for an explicit migration, minimum-diff must not
   preserve architecture the design removes. Ask *"who owns this knowledge?"* and
   move validation/configuration/behavior to that owner rather than hardcoding
   it in generic Core.
6. **Do not stop at scaffolding.** "New manager/interface/schema/tests exist" is
   not completion. The new architecture must **replace the old production path**:
   require *production depends on the new abstraction*, not merely that it exists.
7. **Avoid dual sources of truth.** By the end there must not be two
   authoritative representations of one concept (e.g. new plugin registry + old
   central registry, new schema + legacy validation table). If the design inverts
   ownership, retire the old authoritative path after migrating its information.
8. **Migrate information before deleting its container.** Follow:
   `inventory → classify ownership → migrate → switch consumers → verify semantic
   preservation → remove old source of truth`. For each migrated field know old
   location, meaning, new owner, new location, and verification evidence. A
   deleted file with lost behavior/metadata is a failed migration.
9. **Prefer vertical-slice migration.** Migrate one complete real implementation
   end-to-end (discovery → config/schema → runtime → output → presentation →
   doctor → tests) before bulk-moving; treat it as reference architecture.
   Synthetic fixtures do not replace one real production integration.
10. **Make acceptance criteria executable.** Turn architectural requirements into
    commands/tests (e.g. pytest for zero-plugin startup, architecture-boundary
    checks) rather than subjective "looks clean". A failing executable gate means
    the refactor is incomplete.
11. **Self-authored tests are insufficient evidence.** Also test the negative
    (old architecture no longer required) and integration (real production path
    uses the new architecture) conditions.
12. **Maintain explicit phases.** Track phases (inventory/design validation →
    generic contracts → reference-implementation migration → production switch →
    bulk migration → old-architecture removal → doctor/architecture validation →
    full regression). Record the active phase in `IMPLEMENTATION_STATE.md`; do not
    jump to cleanup before the migrated path is operational.
13. **Never declare completion with unchecked items.** Finish when all checklist
    items are complete or a genuine external blocker stops work (missing
    credentials/service/proprietary dep, or a missing user decision on genuinely
    ambiguous behavior). Large scope, failing tests, complexity, and long diffs
    are not blockers — continue.
14. **Before finalizing, run an architecture audit.** Search for remnants (old
    loaders, registries, legacy config keys, domain identifiers in generic
    modules, special-case branches, compatibility fallbacks, duplicate truth);
    require a reason for every intentional remainder.
15. **Produce a final acceptance report** into `IMPLEMENTATION_STATE.md`:
    architecture changed, old truth retired, where migrated info now lives, which
    real implementation proves the design, changed production paths, acceptance
    tests + commands run, passed/failed, and intentional debt.
16. **Definition of done** — a refactor is complete only when all three hold:
    - DESIGN TRUTH: repository structure and dependency direction match the design;
    - EXECUTION TRUTH: `IMPLEMENTATION_STATE.md` has no unresolved required items;
    - MACHINE TRUTH: acceptance tests and architecture gates pass.

---

## Validation checks
- No schema/enum/contract is duplicated into a skill; skills point at canonical code.
- No dual truth: if a concept appears in backend schemas, the skill references it,
  it does not copy it.
- Working tree changes are consistent as a single patch after reconciliation.
- `IMPLEMENTATION_STATE.md` reflects what was actually verified, not planned.

## Boundary
Do not use subagents as a security boundary; do not delegate a dangerous command
to a subagent. Do not escalate permissions to bypass a failing task; default to
`workspace-write` + approval, and stop/ask on uncertain permission.
