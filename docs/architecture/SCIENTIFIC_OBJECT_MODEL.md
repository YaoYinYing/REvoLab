# Scientific Object Model

> **Status: Proposed — pending human architecture review.** Reconciles the
> Scientific Object domain from first
> principles. Replaces the bootstrap's single-table "enum + JSON" model.

## What is a ScientificObject?

A **ScientificObject** is a durable, identifiable, typed node in a project's
scientific context that stands for one concrete scientific thing and carries the
metadata needed to reason about it. It is *not* a folder, a file, or a label — it
is the projection of a scientific entity REvoLab has decided to represent.

### Universal vs type-specific properties

**Universal (the identity & lifecycle spine — true of every object):**
- `series_id` — the stable global UUID (durable conceptual identity; never a path or username)
- `object_type` — the type discriminator (a registry key)
- `title`/`name` — human label (mutable, **not** identity)
- `description`, `created_at`, `updated_at`, `created_by` (audit)

**No `version` column on the series spine** — versioning is `revision_seq` on
`ScientificObjectRevision` (the revision is the versioned record, never a second
`version` number on the series).

**No organization column lives on the universal spine.** Where a global object sits
in a workspace tree is *project-local* placement, recorded by the Project domain as a
folder/container annotation on `ProjectResourceLink`, never as a column on the global
ScientificObject (see *Organization vs scientific relation*).

**Type-specific (the scientific payload — *never* a universal column and *never*
an unvalidated JSON blob):**
- Protein → organism, source sequence ref, chain
- Sequence → kind, bases
- Structure → resolution, method, PDB/external id, coordinates ref, ligand ref
- Variant → ref/alt, position, effect
- Ligand → SMILES, source
- Complex → components
- Dataset / Assay / Construct → their own typed fields

Each type has its own *schema* (its own column set).

---

## How is scientific typing extended?

**Decision: base record + typed extension tables (recommendation C), with a strict
no-unvalidated-JSON policy for the scientific payload.**

```text
scientific_object_series (identity spine — universal fields + object_type, current_revision_id)
    │ 1:*
scientific_object_revision (revision_id, series_id, revision_seq, checksum, immutable content refs)
    │ 1:1
    ├── protein_revision      (revision_id FK — per-type typed columns)
    ├── variant_revision
    ├── structure_revision
    ├── ...
    └── (no typed table for OTHER / generic — allowed but carries almost nothing)
```

The **typed scientific payload hangs on the revision, not the series** (reviewer round
3): each immutable `scientific_object_revision` row (own `revision_id`, `revision_seq`,
`checksum`) carries exactly the typed columns for its content version in a sibling
`*_revision` table keyed by `revision_id`. The series row is only the identity/governance
spine (label, `object_type`, `current_revision_id`) — it never carries scientific payload
columns.

- **Core owns the type registry** — a single Core module mapping
  `object_type → {typed model, view schema, validator, versioned?}`. This is the
  extension mechanism.
- **Runtime plugins do not own columns.** A new typed scientific concept is a
  durable schema decision and is a deliberate, reviewed Core migration — not a hot
  extension point. This avoids the plugin-driven column-migration hazard.
- `object_type` remains on the core row as the discriminator. `OTHER` is an
  explicit fallback for genuinely untyped things, not a dumping ground.

### Alternative shapes considered

| Option | Verdict |
|---|---|
| A. enum + typed JSON | Rejected — weakly typed, unqueryable scientific payload, JSON schema-versioning burden |
| B. registry-driven object types | Rejected for columns — a plugin can't own physical storage without migrations; fragile base class |
| C. base record + typed extension tables | **Adopted** — real columns, real indexes, real validation, no God-object |
| D. hybrid (typed + fallback JSON) | Rejected — two silent storage mechanisms |

---

## Versions and mutation — conceptual identity vs revision identity

**Two distinct identities must not share one UUID** (reviewer finding): a UUID cannot
simultaneously be "the eternal identity of this scientific thing" and "the immutable
identity of a specific snapshot of it". A provenance edge must be able to point at the
**exact immutable revision** that was consumed/produced.

**Frozen physical nomenclature (reviewer finding #9):** the two tables are
`ScientificObjectSeries` (identity column **`series_id`**) and `ScientificObjectRevision`
(identity column **`revision_id`**), both UUID values. The vague `ScientificObject.id`
is **never** used to mean both; a reference always says which one it means (external
IDs/aliases bind `series_id`; provenance edges reference `revision_id`).

- **Conceptual / series identity** (`series_id`) — the eternal identity of "this
  scientific thing" (e.g. the protein, the variant series). It never changes. This is
  what aliases, external IDs, and cross-Project membership bind to.
- **Revision identity** (`revision_id`) — a unique immutable UUID for **one content
  version**. Each content change creates a *new* `revision_id` with its own internal
  UUID; the old revision UUID stays permanently addressable so provenance edges cite
  exactly which revision was used.

**Mapping to the model:**
- The core `scientific_object_series` spine row carries the stable `series_id` (the
  conceptual identity) and the *current* revision marker.
- Each content revision is an independent immutable record with its own
  `revision_id`, immutable content, and content fingerprint (checksum). Revisions
  are ordered by a monotonic `revision_seq` within the series.
- **Content/provenance edges reference `revision_id`, never `series_id`** (the
  conceptual semantic edges `variant_of`/`represents` address `series_id`). "Which
  Structure revision did this Run consume?" resolves unambiguously.
- **`series_id` is the durable identity** cited by aliases/external IDs, used in the
  UI, and addressed by conceptual semantic relations (`variant_of`, `represents`);
  `revision_id` is what content/provenance relations point at (a Decision targets a
  Series or a Revision explicitly typed).

**Principle:** an object's *provenance-relevant content* is immutable once referenced
or once it reflects a real state of the world. Mutable fields are only the
governance/labeling spine (held on the series row, not revisions).

- **Content mutation** → never in-place: create a **new revision** (same conceptual
  identity, new `revision_id`).
- **Governance/label mutation** (rename, fix typo, add alias) → in-place on
  the series spine; not scientific content, not versioned. Re-parenting is **not** a
  series mutation — it is a project-local organization move on the Project's
  `ProjectResourceLink` (its folder/container annotation).

**New revision vs new object:**
- **New revision** when the change keeps the same conceptual identity (re-folded
  Protein, re-refined Structure → new revision of the same Structure series).
- **New object** when the change alters the conceptual identity (a Sequence promoted
  to Protein, a genuinely different design) → a fresh series, related at the Series
  level by `variant_of` / `represents`; any re-expressed content lineage is a
  `derived_from` edge between the specific **Revisions** only (never a Series
  endpoint).

**Enforcement:** a content update to a referenced object is **rejected by a domain
service** unless it produces a new revision. Versioning is **opt-in per type**.
Revisions are immutable INSERT-only rows; a `revision_seq` orders them; the "current"
revision is a derived pointer on the series row. This is lightweight — no event
sourcing or audit-snapshot system.

---

## Aliases, external identifiers, canonical identifiers

Three distinct concepts — never conflated:

```text
canonical identity   → the stable series_id (used for internal citation/linking)
revision identity    → revision_id (what provenance edges point at)
external identities  → (authority/namespace, native_id) pairs, one may be is_canonical
aliases              → search synonyms used to find, never to cite
```

- **Canonical internal identity:** `series_id` only; `name` is a mutable label.
- **External identities:** a first-class `ExternalIdentity` registry whose durable
  key is the **identity authority/namespace** (see `PROVIDER_CAPABILITIES.md` — this
  is *not* the access provider). The external identity itself is uniquely identified
  by its authority + native id, with **no series on the row**:

  ```
  ExternalIdentity(external_identity_id, authority, native_id, kind)
      UNIQUE(authority, native_id)
  ```

  `authority ∈ {uniprot, pdb, doi, pubmed, ...}`; `kind` captures what the identifier
  denotes (e.g. protein, transcript, structure, dataset) where a project cares. One
  registry row exists per external identity, independent of which REvoLab series it
  points at. (This normalizes the bootstrap's ad-hoc plain `provider + external_id`
  into an authority-keyed, integrity-checked registry.)

  **`ExternalIdentity` is the single external identity truth.** The Evidence domain's
  `ExternalReference` does **not** re-store `authority`/`native_id`; it holds an
  `external_identity_id` FK plus resolver/cache metadata (see `EVIDENCE_PROVENANCE.md`
  / `SCIENTIFIC_GRAPH.md`).

- **Series ↔ external identity mapping:** a **separate, non-global-1:1** join table
  links a series to one or more external identities. This mapping is **not** forced to
  be globally 1:1 — it is a normal many-to-many join between series and external
  identities, carrying an optional `qualifier`/`role` (which aspect of the object the
  identifier means) and the `is_canonical` preference **on the mapping** (which
  external identity is the preferred one *for that series*), not on the
  `ExternalIdentity` row:

  ```
  ScientificObjectExternalIdentity(series_id, external_identity_id,
                                   qualifier/role, is_canonical)
  ```

  An external identity maps at the **series level** (conceptual identity), never at
  the revision level — a revision sample is always identified through its owning
  series. Unless a project deliberately asserts otherwise, an authority ID points at
  one REvoLab series; but if a project chooses, the *same* external identity can be
  asserted against (mapped to) multiple series, driven by this mapping table rather
  than by a forced global uniqueness on the object.
- **Aliases:** a separate, search-oriented, mutable, non-unique list (bound to the
  series).

Distinct from external *identity* is the **access/resolver provider** (OpenBio, a
direct UniProt API, REvoCompute) used to *reach* the authority — changing the resolver
must never change the durable identity. See `PROVIDER_CAPABILITIES.md` (#Authority vs
provider).

---

## Organization vs scientific relation

**Critical correction to the bootstrap.** The current model uses `parent_id` as a
self-FK with `cascade="all, delete-orphan"` and treats the tree as *the* scientific
structure. That encodes three false claims:

1. UI nesting implies scientific semantics (a parent "contains" a child).
2. Tree nesting implies destructive ownership (deleting a folder cascades).
3. One tree can express all science — it cannot (a Variant is scientifically a
   Variant-of a Protein but might be filed anywhere for organization).

**Decision — two distinct mechanisms, one project-local and one global:**

- **Organization** (navigation/folders) is **project-local placement, not a property
  of the global object.** It is recorded by the Project domain as a folder/container
  annotation on `ProjectResourceLink` — never as a column on the global series/revision
  row. The same link row carries both "this resource is in this Project's context" and
  "it is filed here in this Project's tree". Reparenting within one Project moves only
  that Project's linkage; another Project can file the same global object anywhere else
  simultaneously. Deleting a folder/container clears **only that Project-local
  annotation**, never the contained objects (they become "unfiled" in that Project) and
  never the object in other Projects. **No delete cascades from organization.**
- **Scientific relation** (typed edges): the typed edge matrix in
  `SCIENTIFIC_GRAPH.md` — global provenance edges (#1–8) plus project-scoped
  knowledge edges (#9–11) — is the **sole** expression of scientific meaning. The
  complete `RelationType` closed enum is defined canonically there (Edges section):
  #1–8 `variant_of`, `derived_from`, `represents`, `evaluates`,
  `consumed_as_input_by`, `produced`, `imported_as`, `generated_by`; #9–11
  `selects`, `supersedes`, `cites`. (`supports`/`contradicts` are **not** edges —
  they are Evidence `polarity` fields; see `SCIENTIFIC_GRAPH.md`.) That single enum is
  generated into the API/Agent/tool contracts. This is where science lives; the graph
  is assembled at the application layer over relational tables (ADR-0003).

So the same global object can be filed at `Targets/T5alphaH` in Project A and at
`Previous work/P450s` in Project B: two project-local placements over one global
scientific identity.

This satisfies the invariant: *UI navigation hierarchy must not automatically imply
scientific semantics or destructive ownership; Project organization is not scientific
semantics.*

---

## Lifecycle rules that fall out

- Content mutations to referenced objects produce a new version/object, never
  in-place.
- Deleting a container deletes only membership, never objects.
- Deleting an object that is a Relation/Evidence/Decision endpoint is **blocked or
  soft-suspended**, never CASCADEd — the destructive `delete-orphan` is removed
  from scientific objects.
- Provider state stays in the provider; only `ExternalIdentity` references live here.
- `series_id` (Series identity) is the durable conceptual identity, never a path or
  username; `revision_id` (Revision identity) is what content/provenance edges address.

Recorded in **ADR-0009**.
