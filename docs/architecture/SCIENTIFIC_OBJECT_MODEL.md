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
- `id` — the stable global UUID (durable identity; never a path or username)
- `object_type` — the type discriminator (a registry key)
- `title`/`name` — human label (mutable, **not** identity)
- `description`, `created_at`, `updated_at`, `created_by` (audit)
- `organization_anchor` — placement in the workspace tree (see *Organization vs
  relation*)
- optional `version` (type-gated; most content types get it)

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
scientific_objects (core spine — universal fields only)
    │ 1:1
    ├── protein_objects     (per-type typed columns)
    ├── variant_objects
    ├── structure_objects
    ├── ...
    └── (no typed table for OTHER / generic — allowed but carries almost nothing)
```

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

- **Conceptual / series identity** (`series_uuid`) — the eternal identity of "this
  scientific thing" (e.g. the protein, the variant series). It never changes. This is
  what aliases, external IDs, and cross-Project membership bind to.
- **Revision identity** (`revision_uuid`) — a unique immutable UUID for **one content
  version**. Each content change creates a *new* `revision_uuid` with its own internal
  UUID; the old revision UUID stays permanently addressable so provenance edges cite
  exactly which revision was used.

**Mapping to the model:**
- The core `scientific_objects` spine row carries the stable `series_uuid` (the
  conceptual identity) and the *current* revision marker.
- Each content revision is an independent immutable record with its own
  `revision_uuid`, immutable content, and content fingerprint (checksum). Revisions
  are ordered by a monotonic `revision_seq` within the series.
- **Provenance edges reference `revision_uuid`, never `series_uuid`.** "Which Structure
  revision did this Run consume?" resolves unambiguously.
- **`series_uuid` is the durable identity** cited by aliases/external IDs and used in
  the UI; `revision_uuid` is what scientific relations and provenance point at.

**Principle:** an object's *provenance-relevant content* is immutable once referenced
or once it reflects a real state of the world. Mutable fields are only the
governance/labeling spine (held on the series row, not revisions).

- **Content mutation** → never in-place: create a **new revision** (same conceptual
  identity, new `revision_uuid`).
- **Governance/label mutation** (rename, fix typo, re-parent, add alias) → in-place on
  the series spine; not scientific content, not versioned.

**New revision vs new object:**
- **New revision** when the change keeps the same conceptual identity (re-folded
  Protein, re-refined Structure → new revision of the same Structure series).
- **New object** when the change alters the conceptual identity (a Sequence promoted
  to Protein, a genuinely different design) → a fresh series + objects linked by a
  `derived_from` / `variant_of` relation.

**Enforcement:** a content update to a referenced object is **rejected by a domain
service** unless it produces a new revision. Versioning is **opt-in per type**.
Revisions are immutable INSERT-only rows; a `revision_seq` orders them; the "current"
revision is a derived pointer on the series row. This is lightweight — no event
sourcing or audit-snapshot system.

---

## Aliases, external identifiers, canonical identifiers

Three distinct concepts — never conflated:

```text
canonical identity   → the stable series_uuid (used for internal citation/linking)
revision identity    → revision_uuid (what provenance edges point at)
external identities  → (authority/namespace, native_id) pairs, one may be is_canonical
aliases              → search synonyms used to find, never to cite
```

- **Canonical internal identity:** `series_uuid` only; `name` is a mutable label.
- **External identifiers:** a first-class `ExternalId` registry whose durable key is
  the **identity authority/namespace** (see `PROVIDER_CAPABILITIES.md` — this is
  *not* the access provider):
  `(object_series_uuid, authority, native_id, is_canonical, provenance_ref)`,
  `UNIQUE(authority, native_id)`. `authority ∈ {uniprot, pdb, doi, pubmed, ...}`.
  An authority ID maps to exactly one REvoLab series where we assert it. (This
  normalizes the bootstrap's ad-hoc plain `provider + external_id` into an
  authority-keyed, integrity-checked registry.)
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

**Decision — two distinct mechanisms:**

- **Organization** (navigation/folders): a clearly-labeled containment concept —
  a `Folder`/container node or a `container_membership` model independent of
  scientific edges. Reparenting a folder moves no science; deleting a container
  deletes **only the container link**, never the contained objects (they become
  "unfiled"). **No delete cascades from a container.**
- **Scientific relation** (typed edges): the `Relation` concept is the **sole**
  expression of scientific meaning. The complete `RelationType` closed enum is
  defined canonically in `SCIENTIFIC_GRAPH.md` (Edges section) — including
  `variant_of`, `derived_from`, `represents`, `generated_by`, `evaluates`,
  `supports`, `contradicts`, `selects`, plus the provenance edges `consumed_as_input_by`,
  `produced`, `imported_as`, `cites`, `supersedes`. That single enum is generated into
  the API/Agent/tool contracts. This is where science lives; the graph is assembled at
  the application layer over relational tables (ADR-0003).

This satisfies the invariant: *UI navigation hierarchy must not automatically imply
scientific semantics or destructive ownership.*

---

## Lifecycle rules that fall out

- Content mutations to referenced objects produce a new version/object, never
  in-place.
- Deleting a container deletes only membership, never objects.
- Deleting an object that is a Relation/Evidence/Decision endpoint is **blocked or
  soft-suspended**, never CASCADEd — the destructive `delete-orphan` is removed
  from scientific objects.
- Provider state stays in the provider; only `ExternalId` references live here.
- Object `id` is the only durable identity, never a path or username.

Recorded in **ADR-0009**.
