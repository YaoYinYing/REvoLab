# ADR-0008: Global Resource Identity + Project-Scoped Membership

## Context
Cross-user sharing is a stated requirement. A model where "Project owns every object
directly" forces object duplication to share across projects, which is explicitly
forbidden. The current bootstrap makes Project the owner by destructive cascade
(`cascade="all, delete-orphan"`), which would block sharing if left to harden.

## Decision
Use **global/stable resource identity + project-scoped reference/membership** —
**not** "Project owns every object directly."

- Every scientific object has a global stable UUID identity independent of any
  Project.
- A Project holds membership/link rows and annotation; it does not own object
  lifecycle.
- An object can belong to multiple projects via multiple membership rows (never
  duplication).
- A project can reference an object owned elsewhere — that *is* the sharing
  mechanism.
- Deleting a Project removes links and membership, never the underlying objects or
  their provenance.
- Organization (parent/folder tree) is separated from scientific relation.

## Consequences
- Sharing lands without a destructive migration.
- Provenance stays traversable across projects and survives Project deletion.
- Project is simultaneously a namespace, a membership/security boundary, and a weak
  provenance scope, but each concern evolves independently.

## Rejected alternatives
- Project owns every object directly (forces copy-to-share).
- Copying data into each Project on share (explicitly forbidden).
- Letting the current Project CASCADE ownership survive (locks in accidental
  architecture).
