# ADR-0002: Context, Not Execution

## Context
Scientific projects need execution references but should not duplicate scheduler and task truth.

## Decision
REvoLab owns project context and relationships. Drivers own external capabilities; REvoCompute owns execution.

## Consequences
Core stays provider-neutral and run/artifact state is resolved through drivers.

## Rejected alternatives
Runner, Task, Slurm, Apptainer, scheduler, or execution lifecycle models in Core.
