"""Provider-neutral capability protocols and value objects (Phase 4).

This leaf module defines the executable shapes a Provider Driver must realize for
the Core-owned `CapabilityKind` vocabulary. It imports only the neutral leaves
(`revolab.enums`, `revolab.credentials`) so any driver can implement these
protocols without importing Identity, the Secret store, or the scientific object
domain.

Provider-specific vocabulary (task names, parameter names, runner names,
artifact vocabulary, status strings, scheduler details) is NOT represented here:
it flows as opaque data through the value objects below — `TaskKindRef.kind_id`,
`TaskKindSchema.parameter_schema`, `RunView.status`, `ArtifactHandle.native_id` —
never as Core enums or fields.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from revolab.credentials import CredentialLease
from revolab.enums import CapabilityErrorKind, CapabilityKind, ResourceKind

# Unicode categories that are never part of bounded presentation text: C0/C1
# controls, format characters, surrogates, private use, unassigned.
_STRIPPED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})


def bounded_inert_text(value: Any, limit: int) -> str | None:
    """Normalize ONE untrusted external text field into bounded inert text.

    Every external provider's text is untrusted data. Control/format characters
    are removed (so provider text can never carry terminal escapes or invisible
    instruction-shaping characters), whitespace is collapsed, and the result is
    truncated to `limit`. The caller is responsible for treating the result as
    data — never as markup, HTML, or instructions.

    Shared by every driver so "how provider text is made inert" has exactly one
    definition; it lives in the neutral capability leaf rather than in any one
    driver.
    """
    if not isinstance(value, str):
        return None
    cleaned = "".join(
        " " if unicodedata.category(char) in _STRIPPED_CATEGORIES else char
        for char in value
    )
    collapsed = " ".join(cleaned.split())
    if not collapsed:
        return None
    return collapsed[:limit]


class CapabilityError(Exception):
    """The typed failure boundary for provider invocations.

    A driver (or the Provider invocation layer) raises this with a stable Core
    `kind` (see `CapabilityErrorKind`) and a sanitized, non-secret `message`.
    The message must NEVER contain secret material, an Authorization/X-API-Key
    header value, a `secret_ref`, an upstream response body, or an exception
    traceback. `str`/`repr` are safe by construction and may be logged.
    """

    def __init__(
        self,
        kind: CapabilityErrorKind,
        message: str,
        *,
        provider_key: str,
        capability_kind: CapabilityKind,
        retryable: bool = False,
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.provider_key = provider_key
        self.capability_kind = capability_kind
        self.retryable = retryable
        self.upstream_status = upstream_status

    def __str__(self) -> str:
        return self.message

    def __repr__(self) -> str:
        return (
            f"CapabilityError(kind={self.kind.value!r}, "
            f"provider_key={self.provider_key!r}, capability_kind={self.capability_kind.value!r})"
        )


# ---------------------------------------------------------------------------
# Compute capability
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskKindRef:
    """Discovery-time identity of one provider task kind. `kind_id` is provider
    vocabulary (opaque to Core); everything else is presentation data."""

    kind_id: str
    display_name: str
    description: str | None = None
    category: str | None = None


@dataclass(frozen=True)
class InputSpec:
    """Provider-declared, neutral input-file contract for one task kind.

    The field values (extensions etc.) are provider data returned verbatim; Core
    assigns them no meaning and never interprets them."""

    label: str | None = None
    required: bool = False
    multiple: bool = False
    max_files: int | None = None
    accepted_extensions: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskKindSchema:
    """Schema-as-data for one task kind: a JSON Schema (Draft 2020-12) for the
    task parameters plus a neutral input contract. Both are provider data."""

    kind_id: str
    display_name: str
    description: str | None
    parameter_schema: Mapping[str, Any]
    input_spec: InputSpec


@dataclass(frozen=True)
class ExternalArtifactRef:
    """Durable external artifact identity (no bytes). `native_id`/`version_id`
    are provider-owned identity data, never a filesystem path."""

    authority: str
    native_id: str
    version_id: str = ""
    content_type: str | None = None
    size: int | None = None
    checksum: str | None = None


@dataclass(frozen=True)
class ResolvedInput:
    """One provider-neutral input materialized by Core before a capability call.

    Exactly one of `data` (locally owned bytes, e.g. a ScientificObjectRevision
    serialization or an internal revolab artifact) or `external` (an external
    artifact identity whose bytes stay owned by the provider) is present. `role`
    is the optional role named by the provider task-kind schema.
    """

    role: str | None = None
    filename: str = "input"
    content_type: str | None = None
    data: bytes | None = None
    external: ExternalArtifactRef | None = None


@dataclass(frozen=True)
class InputBinding:
    """REvoLab-neutral input declaration for a compute invocation.

    `kind` is the Core `ResourceKind` of the referenced global resource; only
    `LEGAL_COMPUTE_INPUT_KINDS` are legal; `resource_id` is its opaque
    GlobalResourceRegistry identity; `role` is an optional role named by the
    provider task-kind schema.
    """

    kind: ResourceKind
    resource_id: UUID
    role: str | None = None


# The single canonical "compute input" subset of the registry kinds.
LEGAL_COMPUTE_INPUT_KINDS = frozenset(
    {ResourceKind.SCIENTIFIC_OBJECT_REVISION, ResourceKind.ARTIFACT_REFERENCE}
)


@dataclass(frozen=True)
class RunHandle:
    """The immutable identity card REvoLab stores for an external run. It is a
    reference, never a snapshot of mutable execution state."""

    authority: str
    native_id: str
    task_type: str | None = None


@dataclass(frozen=True)
class RunView:
    """Live, on-demand view of an external run. `status` is provider vocabulary
    (opaque string data); REvoLab never persists it."""

    authority: str
    native_id: str
    status: str
    status_detail: str | None = None


@dataclass(frozen=True)
class ArtifactHandle:
    """One external artifact's immutable identity card (on-demand enumeration).

    `data` is populated only by `ArtifactResolutionCapability.resolve`; the
    enumeration path never downloads bytes."""

    authority: str
    native_id: str
    version_id: str = ""
    content_type: str | None = None
    size: int | None = None
    checksum: str | None = None
    data: bytes | None = field(default=None, repr=False, compare=False)


class ComputeCapability(Protocol):
    """The executable compute boundary a Compute provider driver realizes."""

    provider_key: str
    kind: CapabilityKind

    def list_task_kinds(self, credentials: CredentialLease) -> list[TaskKindRef]: ...

    def task_kind_schema(self, kind_id: str, credentials: CredentialLease) -> TaskKindSchema: ...

    def submit(
        self,
        kind_id: str,
        inputs: Sequence[ResolvedInput],
        params: Mapping[str, Any],
        credentials: CredentialLease,
    ) -> RunHandle: ...

    def get_run(self, native_id: str, credentials: CredentialLease) -> RunView: ...

    def list_artifacts(
        self, native_id: str, credentials: CredentialLease
    ) -> list[ArtifactHandle]: ...


class ArtifactResolutionCapability(Protocol):
    """The executable read boundary for resolving external artifact bytes."""

    provider_key: str
    kind: CapabilityKind

    def resolve(
        self, artifact: ExternalArtifactRef, credentials: CredentialLease
    ) -> ArtifactHandle: ...


class ArtifactPreviewCapability(Protocol):
    """The BOUNDED external-artifact read boundary (Phase 6 inspect_artifact).

    A driver may realize bounded preview alongside `ArtifactResolutionCapability`.
    Phase 6 Agent inspection uses this so a large artifact is never fully
    materialized into ProjectContext/Agent memory: the returned `ArtifactHandle`
    must carry only the requested slice (its `data` is the preview, not the whole
    artifact). If a provider cannot honor a bounded read, inspection fails closed
    rather than falling back to a full `resolve`.
    """

    provider_key: str
    kind: CapabilityKind

    def preview(
        self,
        artifact: ExternalArtifactRef,
        credentials: CredentialLease,
        *,
        offset: int = 0,
        limit: int,
    ) -> ArtifactHandle: ...


# ---------------------------------------------------------------------------
# Literature discovery capability (Phase 13)
#
# The provider-neutral boundary for "discover publications REvoLab does not yet
# know". A provider realizing this protocol returns EPHEMERAL candidates; nothing
# here is durable, and nothing here is Project truth. Provider vocabulary (NCBI
# ESummary field names, PubMed query grammar, HTTP routes) stays inside the
# driver.
# ---------------------------------------------------------------------------

# Provider-neutral ceilings for one discovery call. They are the SINGLE canonical
# values: the driver enforces them at the wire boundary and the application
# service re-applies them to the projection, so a misbehaving driver cannot widen
# the Agent/frontend surface. Deliberately small — Phase 13 is discovery, not
# systematic review.
MAX_LITERATURE_QUERY_CHARS = 300
DEFAULT_LITERATURE_RESULT_LIMIT = 10
MAX_LITERATURE_RESULT_LIMIT = 20
MAX_LITERATURE_TITLE_CHARS = 500
MAX_LITERATURE_AUTHORS = 20
MAX_LITERATURE_AUTHOR_CHARS = 200
MAX_LITERATURE_JOURNAL_CHARS = 200
MAX_LITERATURE_DOI_CHARS = 200


@dataclass(frozen=True)
class LiteratureCandidate:
    """One EPHEMERAL external discovery candidate (Phase 13).

    This is provider-neutral presentation data returned by a read-only external
    lookup. It is deliberately NOT a `LiteratureReference`, `ExternalReference`,
    `Evidence`, `SearchHit`, or Project truth; it is never persisted by searching.
    All text fields are untrusted external data and are bounded by the driver.

    `authority` is the DURABLE identity namespace (e.g. `pubmed`), never the
    resolver/provider key: a future resolver may resolve the same
    `(authority, native_id)` identity without changing any stored reference.
    """

    provider_key: str
    authority: str
    native_id: str
    title: str | None = None
    authors: tuple[str, ...] = ()
    journal: str | None = None
    publication_year: int | None = None
    doi: str | None = None


@dataclass(frozen=True)
class LiteratureSearchResult:
    """The bounded result of one external discovery search (ephemeral)."""

    provider_key: str
    candidates: tuple[LiteratureCandidate, ...]


class LiteratureDiscoveryCapability(Protocol):
    """The executable external-literature discovery boundary.

    `search` is a bounded read-only lookup by opaque provider search text;
    `resolve` re-reads ONE publication by its durable `(authority, native_id)`
    identity so an explicit import never trusts client-supplied bibliographic
    metadata. Neither method persists anything, and neither accepts a URL, host,
    scheme, port, proxy, or HTTP method.
    """

    provider_key: str
    kind: CapabilityKind

    def search(
        self, query: str, limit: int, credentials: CredentialLease
    ) -> LiteratureSearchResult: ...

    def resolve(
        self, authority: str, native_id: str, credentials: CredentialLease
    ) -> LiteratureCandidate: ...


# ---------------------------------------------------------------------------
# Protein discovery capability (Phase 14)
#
# The provider-neutral boundary for "discover biological entities REvoLab does not
# yet know". A provider realizing this protocol returns EPHEMERAL candidates and,
# for an explicit import, a CURRENTLY RE-RESOLVED record. Provider vocabulary
# (UniProt REST routes, UniProtKB query grammar, UniProt JSON field names) stays
# inside the driver.
#
# `search` and `resolve` are both read-only and persist nothing.
# ---------------------------------------------------------------------------

# Provider-neutral ceilings for one protein discovery call. They are the SINGLE
# canonical values: the driver enforces them at the wire boundary and the
# application service re-applies them to the projection, so a misbehaving driver
# cannot widen the Agent/frontend surface.
MAX_PROTEIN_QUERY_CHARS = 300
DEFAULT_PROTEIN_RESULT_LIMIT = 10
MAX_PROTEIN_RESULT_LIMIT = 20
MAX_PROTEIN_NAME_CHARS = 300
MAX_PROTEIN_GENE_NAME_CHARS = 200
MAX_PROTEIN_ORGANISM_NAME_CHARS = 300
MAX_PROTEIN_ENTRY_NAME_CHARS = 100
MAX_PROTEIN_SOURCE_RELEASE_CHARS = 100

# Durable external identity bounds, mirroring the persisted columns
# (`external_identities.authority` varchar(100), `.native_id` varchar(300)).
MAX_PROTEIN_AUTHORITY_CHARS = 100
MAX_PROTEIN_NATIVE_ID_CHARS = 300

# The canonical amino-acid sequence is stored COMPLETE as an immutable Sequence
# revision; this is a persistence bound, never a presentation truncation. It is
# deliberately far above the longest known protein (~35k residues for titin) so a
# legitimate very large protein is never rejected, while an absurd provider body
# still fails closed before persistence.
MAX_PROTEIN_SEQUENCE_CHARS = 100_000

# The amino-acid alphabet accepted for a canonical UniProtKB sequence. This is the
# IUPAC protein alphabet including the sequence-level ambiguity codes that
# genuinely occur in UniProt entries (`B`, `Z`, `X`, `U`, `O`, `J`) and the
# selenocysteine/pyrrolysine letters; over-constraining it would reject legitimate
# rare/ambiguous residues. A canonical UniProtKB sequence is UPPERCASE, so any
# other character — including a lowercase residue letter, whitespace, or markup —
# is malformed provider data and fails closed rather than being silently repaired.
PROTEIN_SEQUENCE_ALPHABET = frozenset("ACDEFGHIKLMNPQRSTVWYBXZUOJ")


@dataclass(frozen=True)
class ProteinCandidate:
    """One EPHEMERAL external protein discovery candidate (Phase 14).

    This is provider-neutral presentation data returned by a read-only external
    lookup. It is deliberately NOT a ScientificObject, `ExternalIdentity`,
    `ExternalReference`, `Evidence`, `SearchHit`, or Project truth, and searching
    never persists it. All text fields are untrusted external data bounded by the
    driver.

    `authority` is the DURABLE identity namespace (e.g. `uniprot`), never the
    resolver/provider key: another resolver may resolve the same
    `(authority, native_id)` identity without changing any stored reference.

    The canonical sequence is deliberately ABSENT: a search result is presentation
    data, and the sequence is only read by the explicit import re-resolution.
    """

    provider_key: str
    authority: str
    native_id: str
    protein_name: str | None = None
    gene_name: str | None = None
    organism_name: str | None = None
    organism_id: int | None = None
    sequence_length: int | None = None
    reviewed: bool | None = None


@dataclass(frozen=True)
class ProteinSearchResult:
    """The bounded result of one external protein discovery search (ephemeral)."""

    provider_key: str
    candidates: tuple[ProteinCandidate, ...]


@dataclass(frozen=True)
class ResolvedProteinRecord:
    """The CURRENT provider record an explicit import re-resolves (Phase 14).

    This is the canonical, provider-neutral scientific snapshot input: everything
    needed to build the Protein + Sequence ScientificObjects, and nothing else.
    It is NOT durable truth by itself — the application service validates it and
    turns it into immutable revisions, an `ExternalReference` snapshot, and typed
    provenance edges. It is never persisted as a whole.

    `canonical_sequence` is the exact canonical (non-isoform) amino-acid sequence.
    `sequence_length` is the provider-reported length and must agree with it.
    """

    provider_key: str
    authority: str
    native_id: str
    canonical_sequence: str
    protein_name: str | None = None
    organism_name: str | None = None
    entry_name: str | None = None
    primary_gene_name: str | None = None
    sequence_length: int | None = None
    reviewed: bool | None = None
    source_release: str | None = None
    source_release_date: str | None = None


class ProteinDiscoveryCapability(Protocol):
    """The executable external-protein discovery + resolution boundary.

    `search` is a bounded read-only lookup by opaque provider search text;
    `resolve` re-reads ONE protein by its durable `(authority, native_id)`
    identity so an explicit import never trusts client-supplied scientific
    metadata. Neither method persists anything, and neither accepts a URL, host,
    scheme, port, proxy, or HTTP method.

    Phase 14 resolves ACTIVE PRIMARY accessions only. A redirect/inactive/
    secondary accession, or an isoform-suffixed accession, fails closed as a
    typed capability error rather than being silently remapped.
    """

    provider_key: str
    kind: CapabilityKind

    def search(
        self, query: str, limit: int, credentials: CredentialLease
    ) -> ProteinSearchResult: ...

    def resolve(
        self, authority: str, native_id: str, credentials: CredentialLease
    ) -> ResolvedProteinRecord: ...


# ---------------------------------------------------------------------------
# Structure discovery capability (Phase 15)
#
# The provider-neutral boundary for "discover PDB archive structures REvoLab does
# not yet know, and take custody of ONE immutable PDBx/mmCIF coordinate snapshot".
# A provider realizing this protocol returns EPHEMERAL candidates and, for an
# explicit import, a CURRENTLY RE-RESOLVED record whose coordinate bytes are the
# exact archive snapshot the import will own. Provider vocabulary (the RCSB Search
# API JSON query DSL, the Data API GraphQL schema, RCSB attribute paths, PDB
# identifier grammar, file-download routes) stays inside the driver.
#
# `search` and `resolve` are both read-only and persist NOTHING: taking byte
# custody is an explicit human Import step performed by the application service,
# never by the capability.
# ---------------------------------------------------------------------------

# Provider-neutral ceilings for one structure discovery call. They are the SINGLE
# canonical values: the driver enforces them at the wire boundary and the
# application service re-applies them to the projection, so a misbehaving driver
# cannot widen the Agent/frontend surface.
MAX_STRUCTURE_QUERY_CHARS = 300
DEFAULT_STRUCTURE_RESULT_LIMIT = 10
MAX_STRUCTURE_RESULT_LIMIT = 20
MAX_STRUCTURE_TITLE_CHARS = 500
MAX_STRUCTURE_METHOD_CHARS = 200
# A deposited entry can legitimately report more than one experimental method
# (e.g. X-ray + neutron). The set is bounded so an absurd provider body cannot
# inflate the normalized payload.
MAX_STRUCTURE_METHODS = 8
MAX_STRUCTURE_REVISION_TEXT_CHARS = 100

# Durable external identity bounds, mirroring the persisted columns
# (`external_identities.authority` varchar(100), `.native_id` varchar(300)).
MAX_STRUCTURE_AUTHORITY_CHARS = 100
MAX_STRUCTURE_NATIVE_ID_CHARS = 300

# The ONE canonical semantic media type REvoLab records for an imported PDBx/mmCIF
# coordinate snapshot. It is a REvoLab assertion, NOT a copy of the transport
# header: the RCSB file-download documentation states that the generic "download"
# short-style URL sets `Content-Type: application/octet-stream`, which describes a
# byte stream and carries no scientific meaning. `chemical/x-cif` is the de-facto
# community media type for CIF-family files (and is the type the RCSB file service
# itself serves for `.cif`); the `x-` prefix marks it as a non-IANA convention, and
# PDBx/mmCIF has no IANA-registered type. The content type is presentation metadata
# over the bytes: durable byte identity is the checksum, and scientific origin is
# the `pdb:<entry>` ExternalIdentity + `imported_as` provenance.
STRUCTURE_COORDINATE_CONTENT_TYPE = "chemical/x-cif"

# The canonical coordinate-format discriminator on a ResolvedStructureRecord.
# Phase 15 imports PDBx/mmCIF only; legacy `.pdb` and BCIF are never imported.
STRUCTURE_COORDINATE_FORMAT = "mmcif"

# The operational/persistence ceiling for ONE imported canonical PDBx/mmCIF
# coordinate snapshot (128 MiB). Observed archive entry files are well under a
# megabyte for ordinary proteins and tens of MiB for the largest ribosomal/viral
# assemblies, so this is comfortably above the largest deposited entry coordinate
# file while bounding the memory and time a single import can consume. A structure
# larger than this fails EXPLICITLY; coordinates are never truncated.
MAX_STRUCTURE_COORDINATE_BYTES = 134_217_728  # 128 MiB


@dataclass(frozen=True)
class StructureCandidate:
    """One EPHEMERAL external structure discovery candidate (Phase 15).

    This is provider-neutral presentation data returned by a read-only external
    lookup. It is deliberately NOT a ScientificObject, `ExternalIdentity`,
    `ExternalReference`, `ArtifactReference`, `Evidence`, `SearchHit`, or Project
    truth, and searching never persists it. It carries NO coordinate bytes. All
    text fields are untrusted external data bounded by the driver.

    `authority` is the DURABLE identity namespace (`pdb`), never the
    resolver/provider key: another resolver may resolve the same
    `(authority, native_id)` identity without changing any stored reference.
    """

    provider_key: str
    authority: str
    native_id: str
    title: str | None = None
    experimental_methods: tuple[str, ...] = ()
    resolution_angstrom: float | None = None
    release_date: str | None = None
    polymer_entity_count: int | None = None


@dataclass(frozen=True)
class StructureSearchResult:
    """The bounded result of one external structure discovery search (ephemeral)."""

    provider_key: str
    candidates: tuple[StructureCandidate, ...]


@dataclass(frozen=True)
class ResolvedStructureRecord:
    """The CURRENT provider record an explicit import re-resolves (Phase 15).

    This is the canonical, provider-neutral scientific snapshot input: everything
    needed to build ONE `Structure` ScientificObject plus its owned coordinate
    artifact, and nothing else. It is NOT durable truth by itself — the
    application service validates it, takes ContentStore custody of
    `coordinate_bytes`, and turns it into an immutable revision, an
    `ExternalReference` snapshot, an internal `ArtifactReference`, and typed
    provenance edges.

    `coordinate_bytes` is the EXACT canonical PDBx/mmCIF archive snapshot for this
    entry. It is hidden from `repr` so it can never leak through a log line, and it
    is bounded by `MAX_STRUCTURE_COORDINATE_BYTES` at the wire boundary.
    """

    provider_key: str
    authority: str
    native_id: str
    coordinate_format: str
    coordinate_bytes: bytes = field(repr=False)
    title: str | None = None
    experimental_methods: tuple[str, ...] = ()
    resolution_angstrom: float | None = None
    entry_revision_major: int | None = None
    entry_revision_minor: int | None = None
    entry_revision_date: str | None = None


class StructureDiscoveryCapability(Protocol):
    """The executable external-structure discovery + resolution boundary.

    `search` is a bounded read-only lookup by opaque provider search text;
    `resolve` re-reads ONE archive entry by its durable `(authority, native_id)`
    identity AND returns that entry's canonical PDBx/mmCIF coordinate bytes, so an
    explicit import never trusts client-supplied scientific metadata or coordinate
    content. Neither method persists anything, and neither accepts a URL, host,
    scheme, port, proxy, or HTTP method.

    Phase 15 resolves EXPERIMENTAL PDB archive entries only: a Computed Structure
    Model (CSM) identifier, an entry whose determination methodology is not
    `experimental`, or an unknown identifier fails closed as a typed capability
    error rather than being imported.
    """

    provider_key: str
    kind: CapabilityKind

    def search(
        self, query: str, limit: int, credentials: CredentialLease
    ) -> StructureSearchResult: ...

    def resolve(
        self, authority: str, native_id: str, credentials: CredentialLease
    ) -> ResolvedStructureRecord: ...
