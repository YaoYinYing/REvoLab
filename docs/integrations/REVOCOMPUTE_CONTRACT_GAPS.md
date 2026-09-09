# REvoCompute Integration Contract Gaps

Status: recorded from the actual public REvoCompute HTTP API (Flask server,
`revocompute/routes.py`, `revocompute/auth.py`, `revocompute/schemas.py`) as
inspected for Phase 4. No REvoCompute internals, database, or filesystem result
directories are used as an integration API.

The REvoLab driver (`backend/src/revolab/drivers/revocompute.py`) is built
against the supported public HTTP surface only. This document records where that
surface does not yet provide a semantic operation REvoLab's accepted contract
would like, and why Phase 4 does not paper over those gaps with REvoLab-side
duplicates of REvoCompute execution truth.

## 1. Cross-Actor sharing / authorization preservation

- **Required semantic operation:** preserve authorization when a run/artifact is
  shared or referenced across Actors. REvoLab Project membership decides who may
  *reference* a run/artifact; the authorization to read the underlying bytes
  must stay in REvoCompute (share a neutral reference, never a copy).
- **Current REvoCompute behavior:** there is no cross-user access-grant
  primitive. `_task_access_allowed` / `_task_mutation_allowed` are strictly
  same-user-or-admin, and `_can_reuse_source_task` requires the source task and
  the destination task to share the same submitter (admin not exempt).
  A second user's task/artifact is always `403`/`404`.
- **Why an internal-module / database / filesystem workaround would violate
  ownership:** REvoLab would have to hold and present another Actor's long-lived
  X-API-Key/Bearer credential to read or reuse their artifacts. That conflates
  "REvoLab Project membership" with "REvoCompute credential ownership" and
  breaks the credential invariant (a provider is callable iff the calling
  Actor's own credential is present).
- **Smallest upstream change:** a REvoCompute access-grant table + endpoint
  (grant Actor B read/reuse of a task/artifact) honoured by
  `_task_access_allowed` and `_can_reuse_source_task`; optionally per-artifact
  signed read tokens.
- **Phase 4 impact:** **blocking only for cross-Actor sharing.** The Phase 4
  vertical slice is single-Actor, and the REvoLab-side reference semantics are
  already correct (an external ArtifactReference is a neutral identity card, not
  a copy). Cross-Actor artifact reuse remains an upstream dependency and is not
  faked.
- **Phase 5 position (unchanged, stated honestly):** Phase 5 lets a REvoLab
  Project share the *identity/context* of a `RunReference`/`ArtifactReference`
  with another Project under REvoLab's own collaboration rules. That is **not**
  an upstream REvoCompute access grant:

  ```text
  REvoLab Project visibility   !=   REvoCompute upstream authorization
  ```

  If Actor B can see a shared ArtifactReference in REvoLab but lacks their own
  external provider authorization, resolving the upstream bytes fails through
  the existing typed Provider/Capability boundary (`CapabilityError(AUTH)`) —
  never by copying the artifact, reusing Actor A's credential, leaking Actor A's
  `CredentialLease`, or faking an upstream grant. This gap remains an upstream
  dependency and is recorded, not papered over.

## 2. Machine-readable JSON Schema for parameters

- **Required semantic operation:** obtain a machine-readable task/input/parameter
  schema so REvoLab Core and the generic frontend can treat it as data.
- **Current REvoCompute behavior:** `GET /compute/api/types/<name>` exposes only
  a flat `params[]` descriptor list
  (`name,type,default,required,description,label,choices,minimum,maximum,step,unit,advanced,help`)
  plus `file_input`/`input_workspace`. The canonical Draft 2020-12 `tt.schema`
  used to validate submissions server-side is never served.
- **Why a workaround would violate ownership:** hardcoding constraints in
  REvoLab would duplicate REvoCompute's parameter schema (second truth) instead
  of consuming the authoritative one.
- **Smallest upstream change:** add `"schema": tt.schema` to the type payload, or
  a `GET /compute/api/types/<name>/schema` endpoint.
- **Phase 4 impact:** **non-blocking.** The translation from REvoCompute's flat
  descriptor to a Draft 2020-12 JSON Schema lives entirely inside the driver
  (`_parameter_schema`), which is exactly where TODO.md instructs translation to
  be isolated. REvoLab Core and the frontend consume the translated schema as
  data.

## 3. Enumerating previously created tasks (run discovery)

- **Required semantic operation:** discover the calling Actor's previous runs so
  they can be referenced (or re-attached) by REvoLab.
- **Current REvoCompute behavior:** no JSON task-list endpoint. The dashboard
  (`GET /compute/dashboard`) is server-rendered HTML; the only task-scoped JSON
  route is `GET /compute/api/tasks/<md5sum>/input`.
- **Why a workaround would violate ownership:** scraping HTML or guessing ids is
  not a supported interface. REvoLab instead retains the immutable `native_id`
  (the task md5sum) of every run it creates, which is sufficient for the
  Phase 4 flow and changes nothing about ownership.
- **Smallest upstream change:** `GET /compute/api/tasks` returning a JSON page of
  the caller's tasks (`md5sum`, `status`, `task_type`).
- **Phase 4 impact:** **non-blocking.** Runs created outside the driver are not
  discoverable until this endpoint exists; this does not affect the Phase 4
  vertical slice.

## 4. Submission / status handshake quirks

- **Required semantic operation:** a stable machine-readable submit and status
  handshake.
- **Current REvoCompute behavior:** submission success is a `302` redirect to
  `/compute/api/running/<md5sum>` with an empty body (an identical re-submit of
  an in-flight task returns `202`). The `failed` run state is returned as HTTP
  `404` with body `{"status":"failed",...}`, while genuine not-found is also
  `404` with `{"status":"not_found",...}`.
- **Why a workaround would violate ownership:** none — this is a contained
  transport quirk, not an ownership boundary.
- **Smallest upstream change:** return `200`/`201` with the JSON task id on a new
  submission, and return `failed` with a machine-readable code on a non-404
  status.
- **Phase 4 impact:** **non-blocking.** The driver does not follow redirects
  automatically (so the credential is never replayed cross-origin); it reads the
  `Location` itself — resolving the relative
  `redirect(f"/compute/api/running/{md5sum}")` against the configured base URL
  and trusting only same-origin targets — then discriminates `failed` from
  `not_found` by response body keys.

## 5. Referencing partially useful outputs of a failed run

- **Required semantic operation:** reference an artifact of a *failed* run as an
  input to a later run (when the artifact is present and valid).
- **Current REvoCompute behavior:** `_resolve_artifact_inputs` requires
  `source.status == "finished"`; a failed or cancelled task's published
  artifacts cannot be reused.
- **Why a workaround would violate ownership:** re-uploading bytes the provider
  already owns would duplicate knowledge instead of referencing it.
- **Smallest upstream change:** permit reuse from `status ∈ {finished, failed}`
  when the target manifest entry exists and the artifact checks pass.
- **Phase 4 impact:** **non-blocking.** This is a reviewer-facing convenience for
  future workflows; it has no effect on the Phase 4 finished-run flow.

## 6. ScientificObjectRevision serialization into task input formats

- **Required semantic operation:** submit a `ScientificObjectRevision` as a
  compute input in whatever format the selected task accepts.
- **Current behavior:** a revision is a typed JSONB payload, not bytes. Phase 4
  materializes it as canonical JSON (checksum-verified) at the Core boundary.
  That neutral default is only valid for REvoCompute tasks whose advertised
  `input_extensions` include JSON; a FASTA/PDB task would reject it.
  The driver therefore validates each byte input's file extension against the
  task's advertised `input_extensions` before submission and fails with a typed
  `INVALID_PARAM` rather than sending bytes the provider will reject.
- **Why a workaround would violate ownership:** Core must not learn FASTA/PDB
  serialization (provider/task vocabulary); a per-task, per-object-type
  serializer would also be speculative until a second concrete task needs it.
- **Smallest upstream/architectural change:** either REvoCompute accepts the
  typed JSON for these tasks, or the driver grows an explicit, task-scoped
  serializer (inside the driver, not Core). The practical Phase-4 input for
  non-JSON tasks is a preformatted `ArtifactReference`.
- **Phase 4 impact:** **non-blocking but explicit.** The revision→compute path
  works end-to-end for JSON-accepting tasks (and the in-process fake); for
  non-JSON task kinds the submission fails fast with `INVALID_PARAM` and the
  operator supplies an artifact instead.

