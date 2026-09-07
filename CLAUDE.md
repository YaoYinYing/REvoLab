## Harness execution

- Use `/goal` for substantial multi-round work.
- The primary agent owns architectural integration and workspace writes.
- Use fresh subagents for independent bounded investigation; parallel writes require disjoint ownership.
- Use workflow for bounded fan-out/fan-in, not open-ended autonomy.
- Use Ralph only for explicitly requested fresh-agent convergence/audit after a coherent implementation exists.
- Load project skills from `.agents/skills/`; skills point to canonical schemas instead of duplicating them.
- Completion requires executable acceptance, not model self-assessment.
- Default to workspace-write + approval; never broaden permissions to bypass a failing task.
- Container-engine access is a privileged external capability, NOT a workspace write; only the known repo `docker-compose.yml` for local postgres, with no privileged/host-PID/docker.sock/bind-outside the approved root, and approval when invoking host Docker/Podman.
- Conversation is working memory; repository docs/tests/ADRs/IMPLEMENTATION_STATE are durable project truth.

## REvoLab architecture invariants

The canonical product boundary: REvoLab owns scientific context and relationships;
REvoCompute, REvoDesign, and external providers own their capabilities and execution
truth. See `docs/architecture/SYSTEM_ARCHITECTURE.md` and the ADRs for the full design.

1. Project organization is not scientific semantics (navigation/folders are UX; scientific meaning lives in typed relations).
2. Project context must not duplicate external execution truth (references are immutable identity cards; state is resolved through the driver, never copied).
3. Provider vocabulary cannot leak into Core (Core knows a fixed vocabulary of capability kinds and consumes provider schemas as data).
4. Agent output becomes project truth only through typed, domain-validated operations. Two gates, never conflated: (a) ALL persistence is a typed domain command (no raw writes); (b) the promotion gate applies ONLY to committing a knowledge assertion (`Decision draft → committed`) — not to ordinary object/evidence creation.
5. Scientific provenance must remain traversable after external systems change (references are never deleted, only revoked).
6. Durable identity is an opaque UUID, never a filesystem path or a mutable username; external identity is `(authority, native_id)`, never a resolver/provider. A ScientificObject's conceptual identity (`series_uuid`) is distinct from its immutable revision identity (`revision_uuid`); provenance addresses revisions.
7. Scientific content is immutable once referenced; change is a new version/revision or a superseding record, never in-place.
8. A Project is a namespace and membership boundary, not the owner of objects; deleting a Project removes links, never the underlying objects or provenance. ScientificObjects/references/relations are global; Evidence/Decision are project-scoped.
9. The Agent is a consumer, not an owner: read context -> reason -> propose -> typed tool -> domain validation -> persisted truth.
10. Credentials are owned by the credential store; a provider is callable iff its driver is READY and every required credential kind is present **for the calling Actor** and project policy permits — all queries, never stored truth.

## Engineering principles

General REvoDesign engineering principles (restored per architecture review; kept short
here, not pushed into optional-loaded skills):

- **Smallest durable implementation.** Prefer the smallest change that is a durable
  step forward; avoid scaffolding for hypothetical future requirements.
- **Working vertical slice.** Demonstrate a thin end-to-end path (typed object → relation
  → evidence/decision → UI) before widening the surface.
- **Prefer established libraries** over handwriting infrastructure (Alembic, Pydantic,
  FastAPI/OpenAPI, standard UI components).
- **No speculative abstraction.** Add an abstraction only when the second concrete
  use case forces it; one concrete user is not enough.
- **Single source of truth.** One canonical model/enum/schema per concept; generated
  contracts and skills POINT at it, never copy it.