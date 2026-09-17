"""RCSB PDB structure-discovery provider driver (Phase 15).

The only module allowed to know RCSB PDB API vocabulary: its three fixed
documented hosts, the Search API JSON query DSL, the Data API GraphQL schema and
attribute paths, the wwPDB PDB identifier grammar (including the legacy <->
extended alias), and the PDBx/mmCIF file-download route. Everything above this
module speaks the neutral `revolab.capabilities` value objects
(`StructureCandidate`, `StructureSearchResult`, `ResolvedStructureRecord`). Core
never imports this module.

Current-API inspection (2026-09-17; see
`docs/architecture/EXTERNAL_STRUCTURE_IMPORT.md` section 9): every normative claim
below was read from the official documentation AND live-verified against the
official hosts.

* Fixed hosts (process constants, never caller-controlled):
  `https://search.rcsb.org/`, `https://data.rcsb.org/`, `https://files.rcsb.org/`.
* Search: `POST rcsbsearch/v2/query`. A free-text query is the `full_text` service
  with parameters containing ONLY `value` (it has no `attribute`); attribute search
  is the `text` service with `{attribute, operator, value}`. `return_type=entry`
  returns PDB identifiers only. `request_options.results_content_type` selects
  `experimental` vs `computational` (its documented default is already
  `["experimental"]`, and it is set explicitly here); `paginate.rows` defaults to
  10 and is capped at 10_000. A search with no hits answers **HTTP 204 with an
  empty body**, which is a successful empty result, not an error.
* Data: `POST graphql` with `entries(entry_ids: [...])`, which batch-fetches MANY
  entries in ONE request (runtime cap 1000 ids; Phase 15 requests at most
  `MAX_STRUCTURE_RESULT_LIMIT`). Results are **keyed by `rcsb_id`, never by
  position** (order is not guaranteed), duplicate ids are de-duplicated, and
  unknown ids are silently dropped. A GraphQL error is reported with HTTP 200 and
  an `errors` array, so the envelope must be inspected rather than the status.
  `resolution_combined` is a **[Float] array** that is NOT sorted (multiple values
  appear only for multi-method entries), and it is `null` for NMR/integrative
  structures; no resolution is ever invented.
* Files: `GET download/{entry_id}.cif` on the fixed static-file host returns the
  canonical uncompressed PDBx/mmCIF archive entry coordinate file, with no
  redirect. Both the 4-character id and the documented `pdb_0000<legacy>` extended
  filename form are accepted by this host. Legacy `.pdb`, `.xml`, `.bcif`,
  biological-assembly (`-assembly1`) and header-only variants are deliberately NOT
  used.
* Identifiers: the official extended form is `pdb_` + eight alphanumerics
  (`pdb_[a-z0-9]{8}`, 12 characters); wwPDB documents that every legacy
  4-character id `XXXX` is exactly `pdb_0000xxxx`. The Search and Data APIs accept
  the 4-character form as of 2026-09-17 (live-verified: an extended-only id answers
  `204` / `null` / `404`), so the driver normalizes the documented alias before
  querying. The metadata index is keyed by BOTH forms, so a future switch to extended
  primary ids cannot silently break discovery.
* Durable identity: `durable_entry_id` normalizes every entry to ONE fixed durable
  form — a legacy 4-character id is PROMOTED to its documented `pdb_0000<legacy>`
  extended alias, and an extended id is unchanged. Both spellings stay fully accepted
  at the provider/API boundary, but the returned `native_id` is always the canonical
  durable form, so a provider that switches which alias spelling it reports (or the
  wwPDB switch to extended primary ids) cannot create a second scientific identity. `results_content_type` does NOT exclude integrative entries
  (live-verified), so discovery re-validates the determination methodology on the
  returned METADATA and EXCLUDES any non-experimental entry, exactly like a Computed
  Structure Model hit.
* Computed Structure Models are identified by an `AF_`/`MA_` id prefix AND by
  `rcsb_entry_info.structure_determination_methodology == "computational"`. Phase 15
  excludes them from discovery and rejects them at resolution, so a computed model
  can never receive `authority = "pdb"`.
* Rate policy: the official documentation publishes no numeric quota — only
  "we recommend starting with a handful of requests per second" and that exceeding
  the limit answers `429`. Phase 15 makes exactly TWO bounded API requests per
  search (one Search + one batched Data) and exactly ONE batched Data API request
  plus ONE static-file coordinate download per resolve/import, so no invented pacing
  constant is introduced, nothing is retried, and nothing is paginated or prefetched
  beyond the requested bound.
* Read-only: discovering persists nothing, and `resolve` only RETURNS bytes. Byte
  custody is taken later by the explicit human import.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from revolab.capabilities import (
    MAX_STRUCTURE_COORDINATE_BYTES,
    MAX_STRUCTURE_METHOD_CHARS,
    MAX_STRUCTURE_METHODS,
    MAX_STRUCTURE_QUERY_CHARS,
    MAX_STRUCTURE_RESULT_LIMIT,
    MAX_STRUCTURE_REVISION_TEXT_CHARS,
    MAX_STRUCTURE_TITLE_CHARS,
    STRUCTURE_COORDINATE_FORMAT,
    CapabilityError,
    ResolvedStructureRecord,
    StructureCandidate,
    StructureSearchResult,
    bounded_inert_text,
    canonical_pdb_entry_id,
    durable_pdb_entry_id,
    is_computed_structure_model_id,
    pdb_extended_alias_of_legacy,
    pdb_legacy_alias_of_extended,
    same_pdb_entry_identity,
)
from revolab.credentials import CredentialLease
from revolab.drivers import DriverContext
from revolab.enums import CapabilityErrorKind, CapabilityKind, ProviderRuntimeHealth

RCSB_PROVIDER_KEY = "rcsb"
# The DURABLE identity namespace this driver resolves. It is deliberately NOT the
# provider/resolver key: a future wwPDB/PDBe/PDBj resolver may resolve the same
# `pdb:<entry>` identity without changing any stored ExternalIdentity.
RCSB_AUTHORITY = "pdb"
RCSB_DISPLAY_NAME = "RCSB PDB"
RCSB_DATA_SOURCE_LABEL = "RCSB PDB"
RCSB_DATA_SOURCE_URL = "https://www.rcsb.org/"

# The three fixed documented hosts. Each is a process/deployment constant and
# never caller-configurable per request; one client is bound per host so a
# request path cannot escape its own base URL.
RCSB_SEARCH_BASE_URL = "https://search.rcsb.org/"
RCSB_DATA_BASE_URL = "https://data.rcsb.org/"
RCSB_FILES_BASE_URL = "https://files.rcsb.org/"

_SEARCH_QUERY_PATH = "rcsbsearch/v2/query"
_GRAPHQL_PATH = "graphql"
# Formatted ONLY from an identifier that already passed `_ENTRY_ID_RE`, so the
# path can never contain a separator, query, fragment, or traversal sequence.
_COORDINATE_PATH_TEMPLATE = "download/{entry_id}.cif"

# Bounded JSON response ceiling for the two METADATA APIs. Coordinates have their
# own, much larger, bound (`MAX_STRUCTURE_COORDINATE_BYTES`).
MAX_API_RESPONSE_BYTES = 2_097_152  # 2 MiB

# The RCSB attribute that distinguishes the experimental PDB archive from Computed
# Structure Models and integrative/hybrid structures. Its documented enum is
# `computational | experimental | integrative`.
_METHODOLOGY_ATTRIBUTE = "rcsb_entry_info.structure_determination_methodology"
_EXPERIMENTAL_METHODOLOGY = "experimental"

# The actor-independent health probe. It takes no caller input, exercises the SAME
# Search API path the discovery capability depends on, and reads a single row.
_HEALTH_QUERY = "hemoglobin"

# The mmCIF data-block signature. This is a bounded SIGNATURE CHECK, not a parser:
# it rejects an HTML/error body served with a 200 and a truncated or garbage
# payload. No scientific field is ever read from the coordinate file; every
# scientific value comes from the Data API.
_MMCIF_SIGNATURE = b"data_"

# Every Data API field the neutral record needs, and nothing else: no author lists,
# no entity annotations, no GO/UniProt cross-references, no ligand tables, no
# validation metrics, no experimental detail beyond the method and resolution.
_GRAPHQL_QUERY = """
query StructureEntryMetadata($entry_ids: [String!]!) {
  entries(entry_ids: $entry_ids) {
    rcsb_id
    struct { title }
    exptl { method }
    rcsb_entry_info {
      resolution_combined
      polymer_entity_count
      structure_determination_methodology
    }
    rcsb_accession_info {
      initial_release_date
      major_revision
      minor_revision
      revision_date
    }
  }
}
"""


# The `pdb` archive-entry IDENTITY semantics (grammar, case, the documented
# legacy <-> extended alias, and the ONE durable form) are owned by the neutral
# capability leaf, because the application import boundary must apply exactly the same
# rules to the `ExternalIdentity` lookup/create. This module keeps only the RCSB
# TRANSPORT mapping (`_api_lookup_id`) on top of them; the names are re-exported so the
# driver remains the place a reader looks for "what this provider accepts".
canonical_entry_id = canonical_pdb_entry_id
durable_entry_id = durable_pdb_entry_id
extended_alias_of_legacy = pdb_extended_alias_of_legacy
same_entry_identity = same_pdb_entry_identity
is_computed_model_id = is_computed_structure_model_id


def _api_lookup_id(durable_id: str) -> str:
    """The identifier to send to the RCSB Search/Data APIs.

    Live-verified 2026-09-17: those two services accept the classic four-character id
    but answer `204`/`null`/`404` for an extended id, while the static-file host
    accepts both. The alias rule is official, so a durable id of the documented
    `pdb_0000<legacy>` form is looked up through its legacy alias; any other extended
    id (an extended-only identifier, which does not exist in the archive yet) is sent
    as-is and fails closed when the provider does not know it.
    """
    legacy = pdb_legacy_alias_of_extended(durable_id)
    return legacy if legacy is not None else durable_id


def _has_mmcif_signature(data: bytes) -> bool:
    head = data[:512].lstrip(b"\xef\xbb\xbf \t\r\n")
    return head.startswith(_MMCIF_SIGNATURE)


def _finite_positive(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _positive_int(value: Any) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        return None
    return value


def _non_negative_int(value: Any) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return value


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _entry_title(entry: Mapping[str, Any]) -> str | None:
    return bounded_inert_text(_mapping(entry.get("struct")).get("title"), MAX_STRUCTURE_TITLE_CHARS)


def _entry_methods(entry: Mapping[str, Any]) -> tuple[str, ...] | None:
    """The entry's deposited experimental methods, deterministically normalized.

    Returns None for structurally impossible provider data: an absurd number of
    methods, or an `exptl` collection that is PRESENT but yields no usable method at
    all (an entry that reports experiments but no method is not the same thing as an
    entry that reports none, and must not silently become "no method").
    Deduplication is case-insensitive and the result is SORTED, so the persisted
    representation never depends on arbitrary provider ordering.
    """
    raw = entry.get("exptl")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        return None
    seen: dict[str, str] = {}
    for item in raw:
        method = bounded_inert_text(_mapping(item).get("method"), MAX_STRUCTURE_METHOD_CHARS)
        if method is None:
            continue
        seen.setdefault(method.casefold(), method)
    if len(seen) > MAX_STRUCTURE_METHODS:
        return None
    if raw and not seen:
        return None
    return tuple(seen[key] for key in sorted(seen))


def _entry_resolution_field(entry: Mapping[str, Any]) -> tuple[bool, float | None]:
    """Inspect `rcsb_entry_info.resolution_combined`.

    Returns `(present, value)`. `present` distinguishes **ABSENT/non-applicable**
    (allowed: `null` or an empty array — the honest answer for an NMR or integrative
    structure, so no resolution is ever invented) from **PRESENT but malformed**
    (structurally impossible provider data, which the caller must fail closed on).

    `resolution_combined` is a documented `[Float]` array holding one value per
    contributing method, and it is NOT sorted, so the BEST value (the minimum in
    angstroms) is the conventional headline resolution. Malformed means: a scalar
    where the contract requires an array, or any non-null element that is not a
    finite positive angstrom value (a string, a boolean, zero, a negative number,
    `NaN`, or an infinity). A non-null member that is *present but uninterpretable*
    must NEVER be silently downgraded to "this structure has no resolution", because
    that would turn malformed provider data into a scientifically valid snapshot.
    A `null` element carries no value (the documented item type is nullable) and is
    skipped; an array whose elements are all `null` is non-applicable, not malformed.
    """
    info = _mapping(entry.get("rcsb_entry_info"))
    raw = info.get("resolution_combined")
    if raw is None:
        return False, None
    if not isinstance(raw, list):
        return True, None
    if not raw:
        return False, None
    values: list[float] = []
    for item in raw:
        if item is None:
            continue
        number = _finite_positive(item)
        if number is None:
            return True, None
        values.append(number)
    if not values:
        return False, None
    return False, min(values)


def _entry_polymer_entity_count(entry: Mapping[str, Any]) -> int | None:
    return _positive_int(_mapping(entry.get("rcsb_entry_info")).get("polymer_entity_count"))


def _entry_methodology(entry: Mapping[str, Any]) -> str | None:
    value = _mapping(entry.get("rcsb_entry_info")).get("structure_determination_methodology")
    return value if isinstance(value, str) else None


def _entry_release_date(entry: Mapping[str, Any]) -> str | None:
    return bounded_inert_text(
        _mapping(entry.get("rcsb_accession_info")).get("initial_release_date"),
        MAX_STRUCTURE_REVISION_TEXT_CHARS,
    )


def _entry_revision(entry: Mapping[str, Any]) -> tuple[int | None, int | None, str | None]:
    info = _mapping(entry.get("rcsb_accession_info"))
    return (
        _non_negative_int(info.get("major_revision")),
        _non_negative_int(info.get("minor_revision")),
        bounded_inert_text(info.get("revision_date"), MAX_STRUCTURE_REVISION_TEXT_CHARS),
    )


class RcsbStructureDiscoveryCapability:
    """`CapabilityKind.STRUCTURE_DISCOVERY` realization over the RCSB PDB APIs."""

    provider_key = RCSB_PROVIDER_KEY
    kind = CapabilityKind.STRUCTURE_DISCOVERY

    def __init__(
        self,
        search_client: httpx.Client,
        data_client: httpx.Client,
        files_client: httpx.Client,
    ) -> None:
        self._search = search_client
        self._data = data_client
        self._files = files_client

    # -- discovery ----------------------------------------------------------------

    def search(
        self, query: str, limit: int, credentials: CredentialLease
    ) -> StructureSearchResult:
        """One bounded discovery search: ONE Search API request, then ONE batched
        Data API request — never one metadata request per hit.

        `credentials` is accepted for protocol uniformity (the invocation layer
        always builds a lease) but this provider is unauthenticated: no credential
        kind is required and none is sent on the wire.
        """
        bounded_query = self._bounded_query(query)
        bounded_limit = self._bounded_limit(limit)
        payload = self._post_json(
            self._search,
            _SEARCH_QUERY_PATH,
            self._search_body(bounded_query, bounded_limit),
            allow_no_content=True,
        )
        if payload is None:
            # HTTP 204: the provider answered successfully with an empty result.
            return StructureSearchResult(provider_key=self.provider_key, candidates=())
        identifiers = self._search_identifiers(payload, bounded_limit)
        if not identifiers:
            return StructureSearchResult(provider_key=self.provider_key, candidates=())
        by_id = self._entry_metadata_by_id([_api_lookup_id(entry_id) for entry_id in identifiers])
        candidates: list[StructureCandidate] = []
        for entry_id in identifiers:
            entry = by_id.get(_api_lookup_id(entry_id))
            if entry is None:
                # Search returned an identifier the Data API does not know. That is
                # inconsistent provider data: fail closed rather than silently
                # returning a short list.
                raise self._error(
                    CapabilityErrorKind.UNKNOWN,
                    "the provider search returned an entry with no resolvable metadata",
                )
            confirmed = self._confirmed_entry_id(entry)
            if confirmed is None or not same_entry_identity(confirmed, entry_id):
                raise self._error(
                    CapabilityErrorKind.UNKNOWN,
                    "provider returned a mismatched entry identifier",
                )
            # The experimental-archive constraint is re-validated on the METADATA,
            # never delegated to the upstream query. `results_content_type` does NOT
            # exclude integrative entries (live-verified 2026-09-17), so a
            # non-experimental entry here means the provider returned something an
            # experimental-only query must not yield. It is EXCLUDED exactly like a
            # Computed Structure Model hit — a scoped-out result class is dropped, so
            # one stray non-experimental hit can never make every legitimate hit in the
            # same result set undiscoverable, and a candidate can never carry
            # `authority="pdb"` for a computational or integrative entry. (Resolution
            # still fails closed on a non-experimental entry, so durable truth is
            # protected independently of discovery.)
            if _entry_methodology(entry) != _EXPERIMENTAL_METHODOLOGY:
                continue
            methods = _entry_methods(entry)
            if methods is None:
                raise self._error(
                    CapabilityErrorKind.UNKNOWN,
                    "provider returned an invalid experimental-method list",
                )
            resolution_present, resolution = _entry_resolution_field(entry)
            if resolution_present and resolution is None:
                # Present but malformed: never presented as a valid "no resolution".
                raise self._error(
                    CapabilityErrorKind.UNKNOWN,
                    "provider reported an invalid resolution",
                )
            candidates.append(
                StructureCandidate(
                    provider_key=self.provider_key,
                    authority=RCSB_AUTHORITY,
                    # The CANONICAL DURABLE identity, so a candidate's identity is
                    # exactly what an import of it will create.
                    native_id=confirmed,
                    title=_entry_title(entry),
                    experimental_methods=methods,
                    resolution_angstrom=resolution,
                    release_date=_entry_release_date(entry),
                    polymer_entity_count=_entry_polymer_entity_count(entry),
                )
            )
        return StructureSearchResult(
            provider_key=self.provider_key, candidates=tuple(candidates)
        )

    def resolve(
        self, authority: str, native_id: str, credentials: CredentialLease
    ) -> ResolvedStructureRecord:
        """Re-read ONE experimental PDB archive entry AND its canonical mmCIF bytes.

        Nothing about caller-supplied scientific data is trusted: the identity is
        re-resolved at the CURRENT provider, the returned entry must be the SAME
        archive entry that was requested (case- and alias-aware), and the
        coordinate bytes are streamed from the fixed file host under a hard bound.
        A Computed Structure Model, an entry that is not an experimental archive
        entry, or an unknown identifier fails closed.
        """
        if authority != RCSB_AUTHORITY:
            raise self._error(
                CapabilityErrorKind.INVALID_PARAM,
                "this provider resolves only the pdb identity authority",
            )
        requested = self._resolve_entry_id(native_id)
        lookup = _api_lookup_id(requested)
        entries = self._fetch_entries([lookup])
        if not entries:
            raise self._error(
                CapabilityErrorKind.NOT_FOUND,
                "the PDB entry identifier was not found in the archive",
            )
        entry = entries[0]
        confirmed = self._confirmed_entry_id(entry)
        if confirmed is None or not same_entry_identity(confirmed, requested):
            # The provider must hand back the SAME archive entry that was
            # requested. Anything else is a silent identity substitution.
            raise self._error(
                CapabilityErrorKind.UNKNOWN,
                "provider returned a mismatched entry identifier",
            )
        methodology = _entry_methodology(entry)
        if methodology != _EXPERIMENTAL_METHODOLOGY:
            # Phase 15 imports the experimental PDB archive only: a Computed
            # Structure Model or an integrative/hybrid structure is a named
            # deferral, never silently imported as a PDB archive entry.
            raise self._error(
                CapabilityErrorKind.INVALID_PARAM,
                "only experimental PDB archive entries are supported; Computed "
                "Structure Models and integrative structures are not imported",
            )
        methods = _entry_methods(entry)
        if methods is None:
            raise self._error(
                CapabilityErrorKind.UNKNOWN,
                "provider returned an invalid experimental-method list",
            )
        resolution_present, resolution = _entry_resolution_field(entry)
        if resolution_present and resolution is None:
            # Present but malformed: an NMR/integrative structure reports `null`, so a
            # malformed value must never become a scientifically valid absence.
            raise self._error(
                CapabilityErrorKind.UNKNOWN, "provider reported an invalid resolution"
            )
        major, minor, revision_date = _entry_revision(entry)
        coordinate_bytes = self._download_coordinate_bytes(confirmed)
        return ResolvedStructureRecord(
            provider_key=self.provider_key,
            authority=RCSB_AUTHORITY,
            # The CANONICAL DURABLE identity, never the raw caller spelling and never
            # whichever alias spelling THIS response happened to use.
            native_id=confirmed,
            coordinate_format=STRUCTURE_COORDINATE_FORMAT,
            coordinate_bytes=coordinate_bytes,
            title=_entry_title(entry),
            experimental_methods=methods,
            resolution_angstrom=resolution,
            entry_revision_major=major,
            entry_revision_minor=minor,
            entry_revision_date=revision_date,
        )

    # -- bounds / validation ------------------------------------------------------

    @staticmethod
    def _confirmed_entry_id(entry: Mapping[str, Any]) -> str | None:
        """The entry's canonical DURABLE identity (`durable_entry_id`).

        The provider's `rcsb_id` is only its presentation of the entry; the durable
        identity is normalized so it cannot change when the provider switches which
        documented alias spelling it returns.
        """
        return durable_entry_id(entry.get("rcsb_id"))

    @staticmethod
    def _resolve_entry_id(native_id: Any) -> str:
        """Validate the caller-supplied durable identifier.

        A Computed Structure Model identifier is an explicit INVALID_PARAM naming
        the deferral (never a silent strip or a silent PDB identity), and any other
        non-PDB identifier is INVALID_PARAM. Both official PDB forms are accepted,
        including an extended-only identifier that has no legacy alias — Phase 15
        never assumes PDB identifiers are four characters.
        """
        if is_computed_model_id(native_id):
            raise CapabilityError(
                CapabilityErrorKind.INVALID_PARAM,
                "Computed Structure Model identifiers are not PDB archive entries; "
                "only experimental PDB archive entries can be imported",
                provider_key=RCSB_PROVIDER_KEY,
                capability_kind=CapabilityKind.STRUCTURE_DISCOVERY,
            )
        identifier = canonical_entry_id(native_id)
        if identifier is None:
            raise CapabilityError(
                CapabilityErrorKind.INVALID_PARAM,
                "invalid PDB entry identifier",
                provider_key=RCSB_PROVIDER_KEY,
                capability_kind=CapabilityKind.STRUCTURE_DISCOVERY,
            )
        return identifier

    @staticmethod
    def _bounded_query(query: Any) -> str:
        if not isinstance(query, str):
            raise CapabilityError(
                CapabilityErrorKind.INVALID_PARAM,
                "search query must be text",
                provider_key=RCSB_PROVIDER_KEY,
                capability_kind=CapabilityKind.STRUCTURE_DISCOVERY,
            )
        text = query.strip()
        if not text or len(text) > MAX_STRUCTURE_QUERY_CHARS:
            raise CapabilityError(
                CapabilityErrorKind.INVALID_PARAM,
                "search query is empty or exceeds the supported length",
                provider_key=RCSB_PROVIDER_KEY,
                capability_kind=CapabilityKind.STRUCTURE_DISCOVERY,
            )
        return text

    @staticmethod
    def _bounded_limit(limit: Any) -> int:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise CapabilityError(
                CapabilityErrorKind.INVALID_PARAM,
                "result limit must be a positive integer",
                provider_key=RCSB_PROVIDER_KEY,
                capability_kind=CapabilityKind.STRUCTURE_DISCOVERY,
            )
        return min(limit, MAX_STRUCTURE_RESULT_LIMIT)

    @staticmethod
    def _search_body(query: str, limit: int) -> dict[str, Any]:
        """The Search API request: bounded free text AND an experimental-only filter.

        The free-text input is placed in the `full_text` service's single `value`
        parameter (it has no `attribute`), so no raw RCSB query DSL, JSON AST,
        GraphQL, field path, URL, or endpoint is ever accepted from the caller. The
        methodology predicate and `results_content_type` are belt-and-braces
        constraints that keep Computed Structure Models out of discovery.
        """
        return {
            "query": {
                "type": "group",
                "logical_operator": "and",
                "nodes": [
                    {
                        "type": "terminal",
                        "service": "full_text",
                        "parameters": {"value": query},
                    },
                    {
                        "type": "terminal",
                        "service": "text",
                        "parameters": {
                            "attribute": _METHODOLOGY_ATTRIBUTE,
                            "operator": "exact_match",
                            "value": _EXPERIMENTAL_METHODOLOGY,
                        },
                    },
                ],
            },
            "return_type": "entry",
            "request_options": {
                "paginate": {"start": 0, "rows": limit},
                "results_content_type": [_EXPERIMENTAL_METHODOLOGY],
                "results_verbosity": "minimal",
            },
        }

    def _search_identifiers(self, payload: Mapping[str, Any], limit: int) -> list[str]:
        """Extract and validate the bounded, PDB-only, order-preserving hit list.

        Accepts BOTH documented result shapes (a bare string under `compact`
        verbosity and an `{identifier, score}` object under `minimal`), and returns
        each hit's CANONICAL DURABLE identity (`durable_entry_id`), so discovery works
        entirely in durable-identity space and dedupes the two documented spellings of
        one entry. A Computed Structure Model hit is DROPPED (a legitimate provider
        result class that Phase 15 deliberately scopes out); any other identifier that
        is not a valid PDB archive entry identifier is structurally impossible data and
        fails closed rather than being forwarded.
        """
        raw = payload.get("result_set")
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise self._error(
                CapabilityErrorKind.UNKNOWN, "unexpected provider search envelope"
            )
        identifiers: list[str] = []
        seen: set[str] = set()
        for item in raw:
            if isinstance(item, str):
                value: Any = item
            elif isinstance(item, Mapping):
                value = item.get("identifier")
            else:
                raise self._error(
                    CapabilityErrorKind.UNKNOWN, "unexpected provider search result shape"
                )
            if is_computed_model_id(value):
                continue
            entry_id = durable_entry_id(value)
            if entry_id is None:
                raise self._error(
                    CapabilityErrorKind.UNKNOWN,
                    "unexpected provider entry identifier in the search result",
                )
            if entry_id in seen:
                continue
            seen.add(entry_id)
            identifiers.append(entry_id)
            if len(identifiers) >= limit:
                break
        return identifiers

    def _entry_metadata_by_id(
        self, lookup_ids: Sequence[str]
    ) -> dict[str, Mapping[str, Any]]:
        """ONE batched metadata request, keyed by BOTH the durable id and its API alias.

        The Data API keys entries by its own canonical `rcsb_id`, while the Search API
        returns whatever id form it uses. Indexing each entry under its durable
        identity AND its documented API-lookup alias means a future wwPDB switch to
        extended primary ids cannot silently turn a search hit into "no resolvable
        metadata".
        """
        entries = self._fetch_entries(lookup_ids)
        by_id: dict[str, Mapping[str, Any]] = {}
        for entry in entries:
            confirmed = self._confirmed_entry_id(entry)
            if confirmed is not None:
                by_id[confirmed] = entry
                by_id.setdefault(_api_lookup_id(confirmed), entry)
        return by_id

    def _fetch_entries(self, entry_ids: Sequence[str]) -> list[Mapping[str, Any]]:
        """Batch-fetch entry records in ONE GraphQL request.

        The GraphQL document and its variables are built here from module
        constants; no caller input ever reaches the query text (the ids travel as
        bound variables and have already passed the identifier grammar). The
        response envelope is inspected rather than trusted: a GraphQL `errors`
        array, a non-object `data`, or a malformed entry fails closed, because
        RCSB reports GraphQL errors with HTTP 200.
        """
        if not entry_ids:
            return []
        payload = self._post_json(
            self._data,
            _GRAPHQL_PATH,
            {"query": _GRAPHQL_QUERY, "variables": {"entry_ids": list(entry_ids)}},
        )
        if payload is None:
            return []
        errors = payload.get("errors")
        if errors:
            raise self._error(
                CapabilityErrorKind.UNKNOWN, "the provider rejected the metadata request"
            )
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise self._error(
                CapabilityErrorKind.UNKNOWN, "unexpected provider metadata envelope"
            )
        entries = data.get("entries")
        if entries is None:
            return []
        if not isinstance(entries, list):
            raise self._error(
                CapabilityErrorKind.UNKNOWN, "unexpected provider metadata result shape"
            )
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise self._error(
                    CapabilityErrorKind.UNKNOWN, "unexpected provider entry shape"
                )
        return list(entries)

    # -- transport ----------------------------------------------------------------

    def _download_coordinate_bytes(self, entry_id: str) -> bytes:
        """Stream ONE canonical PDBx/mmCIF snapshot from the FIXED file host.

        The path is a module template formatted from an identifier that already
        passed the official grammar, lowercased to the documented archive filename
        form. Ownership: the streamed response is closed EXACTLY ONCE by the single
        `try/finally`, which owns the status check, the bounded read, and the
        non-empty/signature checks.
        """
        path = _COORDINATE_PATH_TEMPLATE.format(entry_id=entry_id.lower())
        request = self._files.build_request("GET", path)
        response = self._send(self._files, request)
        try:
            self._raise_on_error(response)
            data = self._read_bounded(response, MAX_STRUCTURE_COORDINATE_BYTES)
        finally:
            response.close()
        if not data:
            raise self._error(
                CapabilityErrorKind.UNKNOWN, "the provider returned an empty coordinate file"
            )
        if not _has_mmcif_signature(data):
            # A bounded signature check, not a parser: coordinate bytes are never
            # interpreted here, and every scientific value comes from the Data API.
            raise self._error(
                CapabilityErrorKind.UNKNOWN,
                "the provider returned a coordinate file that is not PDBx/mmCIF",
            )
        return data

    def _post_json(
        self,
        client: httpx.Client,
        path: str,
        body: Mapping[str, Any],
        *,
        allow_no_content: bool = False,
    ) -> Mapping[str, Any] | None:
        """One bounded, read-only POST against a FIXED RCSB host.

        The body is serialized by httpx and the path is a module constant, so no
        caller input can shape the URL. Redirects are not followed, so an
        unexpected redirect surfaces as a typed failure instead of being
        transparently chased to another host. `None` is returned only for an
        explicitly allowed HTTP 204 (a successful empty search result).
        """
        request = client.build_request("POST", path, json=body)
        response = self._send(client, request)
        try:
            if allow_no_content and response.status_code == 204:
                return None
            self._raise_on_error(response)
            raw = self._read_bounded(response, MAX_API_RESPONSE_BYTES)
        finally:
            response.close()
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError, RecursionError) as exc:
            # `RecursionError` covers pathologically nested JSON: it is unexpected
            # provider data, not a caller error, and must stay a typed failure.
            raise self._error(
                CapabilityErrorKind.UNKNOWN, "provider returned a non-JSON response"
            ) from exc
        if not isinstance(payload, Mapping):
            raise self._error(
                CapabilityErrorKind.UNKNOWN, "unexpected provider payload shape"
            )
        return payload

    def _send(self, client: httpx.Client, request: httpx.Request) -> httpx.Response:
        try:
            return client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            raise self._error(
                CapabilityErrorKind.NETWORK, "the provider request timed out", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            # `httpx.TransportError` AND its siblings (e.g. a malformed proxy/
            # protocol error) are provider-transport failures, never a 500.
            raise self._error(
                CapabilityErrorKind.NETWORK, "the provider is unreachable", retryable=True
            ) from exc
        except Exception as exc:
            raise self._error(
                CapabilityErrorKind.UNKNOWN, "the provider request failed"
            ) from exc

    def _read_bounded(self, response: httpx.Response, bound: int) -> bytes:
        """Read at most `bound` bytes from an already-open stream.

        It deliberately does NOT close the response: the caller owns exactly one
        close in a `finally`, so ownership does not depend on which branch raised.
        An oversized body is ABANDONED rather than buffered, and is never
        truncated: an over-ceiling structure fails explicitly.
        """
        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > bound:
                    raise self._error(
                        CapabilityErrorKind.PROVIDER_UNAVAILABLE,
                        "the provider response exceeded the configured size bound",
                    )
                chunks.append(chunk)
        except CapabilityError:
            raise
        except httpx.HTTPError as exc:
            # Includes `httpx.DecodingError` (a sibling of TransportError) raised
            # for a malformed/truncated `Content-Encoding`: a 2xx provider body
            # that cannot be decoded is a typed provider failure, not a 500.
            raise self._error(
                CapabilityErrorKind.NETWORK,
                "the provider response could not be read",
                retryable=True,
            ) from exc
        except Exception as exc:
            raise self._error(
                CapabilityErrorKind.UNKNOWN, "the provider response could not be read"
            ) from exc
        return b"".join(chunks)

    def _raise_on_error(self, response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if 300 <= status < 400:
            # Redirects are deliberately not followed: the fixed-host boundary is
            # absolute, so an unexpected redirect is a typed failure rather than a
            # transport detail.
            raise self._error(
                CapabilityErrorKind.UNKNOWN,
                "the provider returned an unexpected redirect",
                upstream_status=status,
            )
        if status in {401, 403}:
            raise self._error(
                CapabilityErrorKind.AUTH,
                "the provider rejected the request",
                upstream_status=status,
            )
        if status in {404, 410}:
            raise self._error(
                CapabilityErrorKind.NOT_FOUND,
                "the provider identity was not found",
                upstream_status=status,
            )
        if status in {400, 422}:
            raise self._error(
                CapabilityErrorKind.INVALID_PARAM,
                "the provider rejected the request parameters",
                upstream_status=status,
            )
        if status == 429 or status >= 500:
            raise self._error(
                CapabilityErrorKind.PROVIDER_UNAVAILABLE,
                "the provider is temporarily unavailable",
                retryable=True,
                upstream_status=status,
            )
        raise self._error(
            CapabilityErrorKind.UNKNOWN,
            "unexpected provider response status",
            upstream_status=status,
        )

    def _error(
        self,
        kind: CapabilityErrorKind,
        message: str,
        *,
        retryable: bool = False,
        upstream_status: int | None = None,
    ) -> CapabilityError:
        # Messages are deliberately generic: no URL, no query text, no entry id,
        # no upstream body, and no stack trace crosses this boundary.
        return CapabilityError(
            kind,
            message,
            provider_key=RCSB_PROVIDER_KEY,
            capability_kind=CapabilityKind.STRUCTURE_DISCOVERY,
            retryable=retryable,
            upstream_status=upstream_status,
        )


class RcsbDriver:
    """RCSB PDB provider driver: structure discovery over three fixed hosts."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self.name = RCSB_PROVIDER_KEY
        self.display_name = RCSB_DISPLAY_NAME
        self.description: str | None = (
            "RCSB PDB structure discovery over the official Search, Data and file "
            "services (read-only PDB archive entry metadata and canonical "
            "PDBx/mmCIF coordinates)."
        )
        self.authorities: tuple[str, ...] = (RCSB_AUTHORITY,)
        # The public RCSB PDB APIs are unauthenticated and there is no API-key
        # feature in this slice: no credential kind is required or sent.
        self.required_credential_kinds: tuple[str, ...] = ()
        self._transport = transport
        self._search_client: httpx.Client | None = None
        self._data_client: httpx.Client | None = None
        self._files_client: httpx.Client | None = None
        self._timeout = 15.0
        self.capabilities: Mapping[CapabilityKind, Any] = {}

    def _make_client(self, base_url: str) -> httpx.Client:
        # Redirects are OFF so the fixed-host boundary is absolute; `trust_env=False`
        # makes the "no caller/deployment proxy" rule literal (ambient HTTPS_PROXY/
        # ALL_PROXY or ~/.netrc must not silently reroute the fixed upstream).
        return httpx.Client(
            base_url=base_url,
            timeout=self._timeout,
            follow_redirects=False,
            trust_env=False,
            transport=self._transport,
        )

    def start(self, context: DriverContext) -> None:
        self._timeout = float(context.settings.get("rcsb_timeout_seconds", 15.0))
        self._search_client = self._make_client(RCSB_SEARCH_BASE_URL)
        self._data_client = self._make_client(RCSB_DATA_BASE_URL)
        self._files_client = self._make_client(RCSB_FILES_BASE_URL)
        self.capabilities = {
            CapabilityKind.STRUCTURE_DISCOVERY: RcsbStructureDiscoveryCapability(
                self._search_client, self._data_client, self._files_client
            )
        }

    def stop(self) -> None:
        for client in (self._search_client, self._data_client, self._files_client):
            if client is not None:
                client.close()
        self._search_client = None
        self._data_client = None
        self._files_client = None
        self.capabilities = {}

    def probe_health(self) -> ProviderRuntimeHealth:
        """Actor-independent runtime probe against the fixed Search host.

        A transport/decoding failure is `UNREACHABLE`; a non-2xx/204 response is
        `DEGRADED` (the provider answered but unhealthy). The probe accepts no
        caller input, requests a single row, and reads at most
        `MAX_API_RESPONSE_BYTES`, so an unhealthy upstream can neither stream an
        unbounded body into memory nor influence the request shape. This never
        raises and never leaks an upstream body.
        """
        client = self._search_client
        if client is None:
            return ProviderRuntimeHealth.UNREACHABLE
        body: dict[str, Any] = {
            "query": {
                "type": "terminal",
                "service": "full_text",
                "parameters": {"value": _HEALTH_QUERY},
            },
            "return_type": "entry",
            "request_options": {
                "paginate": {"start": 0, "rows": 1},
                "results_content_type": [_EXPERIMENTAL_METHODOLOGY],
            },
        }
        try:
            request = client.build_request(
                "POST", _SEARCH_QUERY_PATH, json=body, timeout=min(self._timeout, 10.0)
            )
            response = client.send(request, stream=True)
        except Exception:
            return ProviderRuntimeHealth.UNREACHABLE
        try:
            status = response.status_code
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_API_RESPONSE_BYTES:
                    return ProviderRuntimeHealth.DEGRADED
        except Exception:
            return ProviderRuntimeHealth.UNREACHABLE
        finally:
            response.close()
        healthy = 200 <= status < 300 or status == 204
        return ProviderRuntimeHealth.READY if healthy else ProviderRuntimeHealth.DEGRADED


__all__ = [
    "MAX_API_RESPONSE_BYTES",
    "RCSB_AUTHORITY",
    "RCSB_DATA_BASE_URL",
    "RCSB_DATA_SOURCE_LABEL",
    "RCSB_DATA_SOURCE_URL",
    "RCSB_DISPLAY_NAME",
    "RCSB_FILES_BASE_URL",
    "RCSB_PROVIDER_KEY",
    "RCSB_SEARCH_BASE_URL",
    "RcsbDriver",
    "RcsbStructureDiscoveryCapability",
    "canonical_entry_id",
    "durable_entry_id",
    "extended_alias_of_legacy",
    "is_computed_model_id",
    "same_entry_identity",
]
