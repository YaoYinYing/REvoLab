---
name: security-boundaries
version: 0.1.0
description: Enforce security and capability boundaries — authorization projection, provider isolation, deletion invariants, and privileged external capabilities.
---

# Security Boundaries

## When to use
Use when touching anything that crosses a trust or ownership boundary: authorization
and membership, provider/credential handling, deletion of projects or resources,
container/daemon execution, or any operation that could escape the filesystem workspace.

## Canonical sources
Read `CLAUDE.md` (short invariants) and the authority docs:
`docs/architecture/COLLABORATION_IDENTITY.md` and `ADR-0008` (authorization projection,
ProjectResourceLink, tombstone deletion); `ADR-0012` / `PROVIDER_CAPABILITIES.md`
(provider runtime health vs actor availability); `docs/agents/HARNESS_OPERATING_MODEL.md`
(safety plane).

## Invariants to hold
1. **Global identity is not global readability.** Being able to address a resource by
   its global UUID does not mean any Actor can project it. Visibility is a computed
   projection per query: `Actor → ProjectMembership → ProjectResourceLink → visible
   resource → visible provenance edge`. It is never a stored per-object ACL; it is the
   statement "this Project's context includes these global resources."
2. **Provider vocabulary and credentials never leak into Core.** Core knows a fixed
   vocabulary of capability kinds; provider schemas are consumed as data; credentials
   are owned by the credential store, never by Core or by the agent. A provider is
   callable iff its driver is READY and every required credential kind is present for
   the calling Actor and policy permits — all queries, never stored truth.
3. **Deletion is referentially valid.** Never hard-delete a row other objects still
   point at. A Project (which owns FK-bearing link rows) is **tombstoned** with
   `deleted_at`; active membership links are hard-deleted; Evidence/Decision are
   archived; global resources (ScientificObjects, relations, external references) are
   left untouched.
4. **Container/daemon access is a privileged external capability, not a workspace
   write.** Only the known repo `docker-compose.yml` for local postgres; no
   `--privileged`, no host PID/IPC, no `docker.sock` mount, no bind outside the approved
   data root; approval required to invoke host Docker/Podman. A sandbox or subagent is
   never a safe proxy for the Docker daemon.
5. **Agent output becomes truth only through typed, domain-validated ops** — all
   persistence is a typed domain command; promotion applies only to committing a
   `Decision draft → committed`.

## Report
Report violations with the concrete location and the invariant broken. Do not "fix"
security by loosening an invariant.
