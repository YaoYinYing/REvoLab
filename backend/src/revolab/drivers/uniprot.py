"""UniProt protein-discovery provider driver (Phase 14).

The only module allowed to know UniProt REST vocabulary: its fixed host, its
`uniprotkb/search` and `uniprotkb/{accession}` routes, its `fields` selector
names, its JSON envelope shape, its entry-type strings, and its
`X-UniProt-Release*` response headers. Everything above this module speaks the
neutral `revolab.capabilities` value objects (`ProteinCandidate`,
`ProteinSearchResult`, `ResolvedProteinRecord`). Core never imports this module.

Current-API inspection (2026-09-16; see
`docs/architecture/EXTERNAL_PROTEIN_IMPORT.md` section 9):

* the official `www.uniprot.org/help/*` pages are a JavaScript single-page
  application, but UniProt serves the IDENTICAL help content as JSON from the
  same origin at `https://rest.uniprot.org/help/<id>`. The official
  `api_queries`, `api_retrieve_entries`, `rest-api-headers`, `accession_numbers`,
  `query-fields`, `return_fields`, `pagination` and `canonical_and_isoforms`
  documents were read there, and every behavioral claim below was additionally
  confirmed by live probes against the official REST API;
* base host `https://rest.uniprot.org/`; the caller may never supply a URL, host,
  scheme, port, proxy, redirect target, or HTTP method;
* `GET uniprotkb/search` — documented parameters `query`, `format`, `fields`
  ("applies to `tsv`, `xslx` and `json` formats only"), `includeIsoform`,
  `compressed`, `size`, `cursor`. It returns `{"results": [...]}`. The `fields`
  selector keeps one bounded result at a few hundred bytes instead of tens of KiB
  of annotation payload, so the driver requests ONLY the neutral fields it needs
  and never sees GO terms, features, comments, or cross-references;
* `includeIsoform` is NOT sent, so the default canonical entry (and canonical
  sequence) is what the provider returns — the documented default;
* `GET uniprotkb/{accession}.json` returns the single entry plus the documented
  `X-UniProt-Release` / `X-UniProt-Release-Date` headers;
* `entryType` is the ONLY reviewedness signal (`UniProtKB reviewed (Swiss-Prot)`
  vs `UniProtKB unreviewed (TrEMBL)`); there is no boolean `reviewed` field in the
  JSON, and the documented `reviewed` return field is a TSV column only;
* inactive/secondary accessions are reported TWO different ways and BOTH are
  handled: a MERGED accession answers the OFFICIALLY DOCUMENTED `303 See Other`
  with a `Location` header, while DELETED and DEMERGED accessions answer `200`
  with an in-body `entryType == "Inactive"` and an `inactiveReason`. There is no
  `redirected` field in the current API;
* NO numeric rate limit is documented (the only `429` statement in the official
  corpus concerns the separate `stream` endpoint). The driver therefore makes
  exactly ONE bounded request per search / per resolve, never retries, never
  prefetches, and translates `429`/`5xx` into a typed retryable failure.
  `Retry-After` is not documented for this API and is deliberately not surfaced:
  nothing in Phase 14 consumes it, and inventing a retry scheduler is explicitly
  out of scope;
* read-only: no sequence/annotation persistence, no crawling, no pagination.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from revolab.capabilities import (
    MAX_PROTEIN_ENTRY_NAME_CHARS,
    MAX_PROTEIN_GENE_NAME_CHARS,
    MAX_PROTEIN_NAME_CHARS,
    MAX_PROTEIN_ORGANISM_NAME_CHARS,
    MAX_PROTEIN_QUERY_CHARS,
    MAX_PROTEIN_RESULT_LIMIT,
    MAX_PROTEIN_SEQUENCE_CHARS,
    MAX_PROTEIN_SOURCE_RELEASE_CHARS,
    PROTEIN_SEQUENCE_ALPHABET,
    CapabilityError,
    ProteinCandidate,
    ProteinSearchResult,
    ResolvedProteinRecord,
    bounded_inert_text,
)
from revolab.credentials import CredentialLease
from revolab.drivers import DriverContext
from revolab.enums import CapabilityErrorKind, CapabilityKind, ProviderRuntimeHealth

UNIPROT_PROVIDER_KEY = "uniprot"
# The DURABLE identity namespace this driver resolves. It is deliberately NOT the
# provider/resolver key: `provider_key == authority` holds for this first provider
# only by coincidence, and a future resolver (a mirror) can resolve the same
# `uniprot:<accession>` identity without changing any stored ExternalIdentity.
UNIPROT_AUTHORITY = "uniprot"
UNIPROT_DISPLAY_NAME = "UniProt"

# The single fixed official REST host. Never caller-configurable per request; the
# base URL is a process/deployment constant.
UNIPROT_REST_BASE_URL = "https://rest.uniprot.org/"
_UNIPROTKB_SEARCH_PATH = "uniprotkb/search"
# Formatted ONLY from an accession that already passed `_ACCESSION_RE`, so the
# path can never contain a separator, query, fragment, or traversal sequence.
_UNIPROTKB_ENTRY_PATH_TEMPLATE = "uniprotkb/{accession}.json"

# Response-byte ceiling. Checked WHILE streaming so an oversized upstream body is
# abandoned rather than buffered into memory. A bounded search result is a few KiB
# and a bounded single entry (one canonical sequence) is far below this.
MAX_RESPONSE_BYTES = 2_097_152  # 2 MiB

# The neutral fields the driver actually uses. Requesting them keeps the upstream
# payload bounded and guarantees Core never receives annotations/features/GO/
# cross-references it must not consume. `entryType` is always returned and is the
# only reviewedness signal, so no `reviewed` column is requested.
_SEARCH_FIELDS = "accession,id,protein_name,gene_primary,organism_name,organism_id,length"
_ENTRY_FIELDS = f"{_SEARCH_FIELDS},sequence"

# The actor-independent health probe. It takes no caller input, exercises the
# SEARCH path the capability actually depends on, and returns ~140 bytes. The
# query is the one the official `rest-api-headers` document uses in its own
# worked `200 OK` example, so it depends on no single entry's lifecycle.
_HEALTH_QUERY = "P53"
_HEALTH_FIELDS = "accession"

# The official UniProtKB accession grammar, quoted verbatim from the official
# `accession_numbers` document (inspected 2026-09-16):
#
#     [OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2}
#
# It is the durable-identity validator: `authority = uniprot`,
# `native_id = primary accession`, 6 or 10 characters, uppercase alphanumerics.
# It was additionally confirmed against the live API for the official examples
# (`P12345`, `A2BC19`, `A0A023GPI8`) and other live accessions (`P99999`,
# `Q92918`, `A0A0A0MS99`).
_ACCESSION_RE = re.compile(
    r"^(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})$"
)
# An isoform identifier (`P12345-2`). The official `alternative_products` document
# defines it as "the primary accession number of the entry, followed by a dash and
# a number", i.e. a SEPARATE layer on top of the accession grammar. Phase 14
# imports the CANONICAL sequence only, so an isoform identifier is rejected
# explicitly instead of being silently stripped to `P12345` (which would import a
# different scientific object than the caller asked for).
_ISOFORM_RE = re.compile(r"^[A-Za-z0-9]{6,10}-[0-9]{1,3}$")

# UniProt `entryType` values (official docs + live inspection 2026-09-16).
_REVIEWED_ENTRY_TYPE_PREFIX = "UniProtKB reviewed"
_UNREVIEWED_ENTRY_TYPE_PREFIX = "UniProtKB unreviewed"
_INACTIVE_ENTRY_TYPE = "Inactive"

_MAX_TAXON_ID = 2_147_483_647


@dataclass(frozen=True)
class ProviderRelease:
    """The bounded upstream release identity of ONE response (never raw headers)."""

    source_release: str | None = None
    source_release_date: str | None = None


def _sanitize_accession(value: Any) -> str | None:
    """Accept exactly an active primary UniProtKB accession, else None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if _ACCESSION_RE.fullmatch(text) else None


def _reviewed(entry_type: Any) -> bool | None:
    if not isinstance(entry_type, str):
        return None
    if entry_type.startswith(_REVIEWED_ENTRY_TYPE_PREFIX):
        return True
    if entry_type.startswith(_UNREVIEWED_ENTRY_TYPE_PREFIX):
        return False
    return None


def _is_inactive(entry_type: Any, entry: Mapping[str, Any]) -> bool:
    """An inactive/deleted/demerged entry is reported IN BODY, not by status."""
    return entry_type == _INACTIVE_ENTRY_TYPE or "inactiveReason" in entry


def _taxon_id(value: Any) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return value if 1 <= value <= _MAX_TAXON_ID else None


def _protein_name(entry: Mapping[str, Any]) -> str | None:
    """The recommended protein name, or None when the entry has only submission
    names (or no protein description at all)."""
    description = entry.get("proteinDescription")
    if not isinstance(description, Mapping):
        return None
    recommended = description.get("recommendedName")
    if not isinstance(recommended, Mapping):
        return None
    full_name = recommended.get("fullName")
    if not isinstance(full_name, Mapping):
        return None
    return bounded_inert_text(full_name.get("value"), MAX_PROTEIN_NAME_CHARS)


def _gene_name(entry: Mapping[str, Any]) -> str | None:
    """The primary gene name of the first gene locus."""
    genes = entry.get("genes")
    if not isinstance(genes, list):
        return None
    for locus in genes:
        if not isinstance(locus, Mapping):
            continue
        gene = locus.get("geneName")
        if not isinstance(gene, Mapping):
            continue
        name = bounded_inert_text(gene.get("value"), MAX_PROTEIN_GENE_NAME_CHARS)
        if name is not None:
            return name
    return None


def _organism(entry: Mapping[str, Any]) -> tuple[str | None, int | None]:
    organism = entry.get("organism")
    if not isinstance(organism, Mapping):
        return None, None
    name = bounded_inert_text(organism.get("scientificName"), MAX_PROTEIN_ORGANISM_NAME_CHARS)
    return name, _taxon_id(organism.get("taxonId"))


def _sequence_length_field(entry: Mapping[str, Any]) -> tuple[bool, int | None]:
    """Inspect the provider's `sequence.length`.

    Returns `(present, value)`. `present` distinguishes **absent** (allowed: no
    length was reported, so no agreement check is possible) from **present but
    malformed** (structurally impossible provider data). Candidate presentation
    tolerates the latter by using `None`; the resolving path fails closed on it, so a
    malformed length can never silently skip the sequence/length agreement check.
    """
    sequence = entry.get("sequence")
    if not isinstance(sequence, Mapping) or "length" not in sequence:
        return False, None
    raw = sequence.get("length")
    if raw is None:
        return False, None
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
        return True, None
    return True, raw


def _bounded_release(value: Any) -> str | None:
    return bounded_inert_text(value, MAX_PROTEIN_SOURCE_RELEASE_CHARS)


class UniProtProteinDiscoveryCapability:
    """`CapabilityKind.PROTEIN_DISCOVERY` realization over the UniProt REST API."""

    provider_key = UNIPROT_PROVIDER_KEY
    kind = CapabilityKind.PROTEIN_DISCOVERY

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    # -- discovery ----------------------------------------------------------------

    def search(
        self, query: str, limit: int, credentials: CredentialLease
    ) -> ProteinSearchResult:
        """One bounded discovery search: exactly ONE `uniprotkb/search` request.

        `credentials` is accepted for protocol uniformity (the invocation layer
        always builds a lease) but Phase 14's provider is unauthenticated: no
        credential kind is required and none is sent on the wire.
        """
        bounded_query = self._bounded_query(query)
        bounded_limit = self._bounded_limit(limit)
        payload, _ = self._get_json(
            _UNIPROTKB_SEARCH_PATH,
            {
                "query": bounded_query,
                "format": "json",
                "size": str(bounded_limit),
                "fields": _SEARCH_FIELDS,
            },
        )
        results = payload.get("results")
        if not isinstance(results, list):
            raise self._error(
                CapabilityErrorKind.UNKNOWN, "unexpected provider search envelope"
            )
        candidates: list[ProteinCandidate] = []
        seen: set[str] = set()
        for raw in results:
            if len(candidates) >= bounded_limit:
                break
            if not isinstance(raw, Mapping):
                raise self._error(
                    CapabilityErrorKind.UNKNOWN,
                    "unexpected provider search result shape",
                )
            if _is_inactive(raw.get("entryType"), raw):
                # A search that returns an inactive entry is provider data this
                # slice cannot represent as an active primary identity. Fail
                # closed rather than forward or silently drop it.
                raise self._error(
                    CapabilityErrorKind.UNKNOWN,
                    "provider search returned an inactive entry",
                )
            accession = _sanitize_accession(raw.get("primaryAccession"))
            if accession is None:
                # An accession that is not a primary UniProtKB accession is
                # structurally impossible data: fail closed rather than forward it.
                raise self._error(
                    CapabilityErrorKind.UNKNOWN,
                    "unexpected provider accession in the search result",
                )
            if accession in seen:
                raise self._error(
                    CapabilityErrorKind.UNKNOWN,
                    "duplicate provider accession in the search result",
                )
            seen.add(accession)
            organism_name, organism_id = _organism(raw)
            candidates.append(
                ProteinCandidate(
                    provider_key=self.provider_key,
                    authority=UNIPROT_AUTHORITY,
                    native_id=accession,
                    protein_name=_protein_name(raw),
                    gene_name=_gene_name(raw),
                    organism_name=organism_name,
                    organism_id=organism_id,
                    # Presentation data: a malformed length is simply omitted.
                    sequence_length=_sequence_length_field(raw)[1],
                    reviewed=_reviewed(raw.get("entryType")),
                )
            )
        return ProteinSearchResult(provider_key=self.provider_key, candidates=tuple(candidates))

    def resolve(
        self, authority: str, native_id: str, credentials: CredentialLease
    ) -> ResolvedProteinRecord:
        """Re-read ONE protein by its durable external identity.

        Nothing about caller-supplied scientific data is trusted: the identity is
        re-resolved at the CURRENT provider, and only ACTIVE PRIMARY accessions
        are accepted. An inactive/deleted/demerged accession, a secondary
        accession that redirects, or an isoform accession fails closed — Phase 14
        never silently remaps a durable identity.
        """
        if authority != UNIPROT_AUTHORITY:
            raise self._error(
                CapabilityErrorKind.INVALID_PARAM,
                "this provider resolves only the uniprot identity authority",
            )
        accession = self._resolve_accession(native_id)
        payload, release = self._get_json(
            _UNIPROTKB_ENTRY_PATH_TEMPLATE.format(accession=accession),
            {"format": "json", "fields": _ENTRY_FIELDS},
        )
        entry_type = payload.get("entryType")
        if _is_inactive(entry_type, payload):
            raise self._error(
                CapabilityErrorKind.NOT_FOUND,
                "the UniProtKB accession is inactive; search for and import the "
                "current active primary accession instead",
            )
        primary = _sanitize_accession(payload.get("primaryAccession"))
        if primary is None or primary != accession:
            # The provider must hand back the EXACT active primary accession that
            # was requested. Anything else is a silent identity remap.
            raise self._error(
                CapabilityErrorKind.UNKNOWN,
                "provider returned a mismatched protein accession",
            )
        sequence = self._canonical_sequence(payload)
        reported_present, reported_length = _sequence_length_field(payload)
        if reported_present and reported_length is None:
            # Fail closed: a PRESENT but invalid length is malformed provider data,
            # never "no length reported".
            raise self._error(
                CapabilityErrorKind.UNKNOWN, "provider reported an invalid sequence length"
            )
        if reported_length is not None and reported_length != len(sequence):
            raise self._error(
                CapabilityErrorKind.UNKNOWN,
                "provider sequence length disagrees with the canonical sequence",
            )
        organism_name, _ = _organism(payload)
        entry_name = bounded_inert_text(payload.get("uniProtkbId"), MAX_PROTEIN_ENTRY_NAME_CHARS)
        return ResolvedProteinRecord(
            provider_key=self.provider_key,
            authority=UNIPROT_AUTHORITY,
            native_id=accession,
            canonical_sequence=sequence,
            protein_name=_protein_name(payload),
            organism_name=organism_name,
            entry_name=entry_name,
            primary_gene_name=_gene_name(payload),
            sequence_length=reported_length,
            reviewed=_reviewed(entry_type),
            source_release=release.source_release,
            source_release_date=release.source_release_date,
        )

    # -- bounds / validation ------------------------------------------------------

    @staticmethod
    def _resolve_accession(native_id: Any) -> str:
        """Validate the caller-supplied durable identifier.

        A malformed identifier is an INVALID_PARAM; an isoform accession is an
        explicit INVALID_PARAM naming the deferral, never a silent strip.
        """
        if isinstance(native_id, str) and _ISOFORM_RE.fullmatch(native_id.strip()):
            raise CapabilityError(
                CapabilityErrorKind.INVALID_PARAM,
                "UniProt isoform accessions are not supported; import the canonical "
                "primary accession",
                provider_key=UNIPROT_PROVIDER_KEY,
                capability_kind=CapabilityKind.PROTEIN_DISCOVERY,
            )
        accession = _sanitize_accession(native_id)
        if accession is None:
            raise CapabilityError(
                CapabilityErrorKind.INVALID_PARAM,
                "invalid UniProtKB primary accession",
                provider_key=UNIPROT_PROVIDER_KEY,
                capability_kind=CapabilityKind.PROTEIN_DISCOVERY,
            )
        return accession

    @staticmethod
    def _canonical_sequence(entry: Mapping[str, Any]) -> str:
        """Extract and validate the canonical (non-isoform) amino-acid sequence.

        Fail-closed rules: non-empty, bounded, and composed ONLY of the accepted
        uppercase amino-acid letters — no whitespace, control character, HTML, or
        markup can survive. Nothing is silently repaired or truncated.
        """
        sequence = entry.get("sequence")
        value = sequence.get("value") if isinstance(sequence, Mapping) else None
        if not isinstance(value, str) or not value:
            raise CapabilityError(
                CapabilityErrorKind.UNKNOWN,
                "provider returned no canonical protein sequence",
                provider_key=UNIPROT_PROVIDER_KEY,
                capability_kind=CapabilityKind.PROTEIN_DISCOVERY,
            )
        if len(value) > MAX_PROTEIN_SEQUENCE_CHARS:
            raise CapabilityError(
                CapabilityErrorKind.PROVIDER_UNAVAILABLE,
                "the provider sequence exceeded the configured size bound",
                provider_key=UNIPROT_PROVIDER_KEY,
                capability_kind=CapabilityKind.PROTEIN_DISCOVERY,
            )
        if not PROTEIN_SEQUENCE_ALPHABET.issuperset(value):
            raise CapabilityError(
                CapabilityErrorKind.UNKNOWN,
                "provider returned a malformed canonical protein sequence",
                provider_key=UNIPROT_PROVIDER_KEY,
                capability_kind=CapabilityKind.PROTEIN_DISCOVERY,
            )
        return value

    @staticmethod
    def _bounded_query(query: Any) -> str:
        if not isinstance(query, str):
            raise CapabilityError(
                CapabilityErrorKind.INVALID_PARAM,
                "search query must be text",
                provider_key=UNIPROT_PROVIDER_KEY,
                capability_kind=CapabilityKind.PROTEIN_DISCOVERY,
            )
        text = query.strip()
        if not text or len(text) > MAX_PROTEIN_QUERY_CHARS:
            raise CapabilityError(
                CapabilityErrorKind.INVALID_PARAM,
                "search query is empty or exceeds the supported length",
                provider_key=UNIPROT_PROVIDER_KEY,
                capability_kind=CapabilityKind.PROTEIN_DISCOVERY,
            )
        return text

    @staticmethod
    def _bounded_limit(limit: Any) -> int:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise CapabilityError(
                CapabilityErrorKind.INVALID_PARAM,
                "result limit must be a positive integer",
                provider_key=UNIPROT_PROVIDER_KEY,
                capability_kind=CapabilityKind.PROTEIN_DISCOVERY,
            )
        return min(limit, MAX_PROTEIN_RESULT_LIMIT)

    # -- transport ----------------------------------------------------------------

    def _get_json(
        self, path: str, params: Mapping[str, str]
    ) -> tuple[Mapping[str, Any], ProviderRelease]:
        """One bounded, read-only GET against the FIXED UniProt REST host.

        `path` is a module constant or a template formatted from an already
        VALIDATED accession, never raw caller text; query text is passed through
        httpx parameter encoding and never concatenated into a URL. Redirects are
        not followed, so a `303` for a secondary/merged accession surfaces as a
        typed failure instead of being transparently resolved to a different
        identity.

        Response ownership: the streamed response is opened by `_send` and closed
        EXACTLY ONCE by the single `try/finally` below, which owns BOTH the status
        handling, the bounded body read, and the release-header extraction.
        """
        request = self._client.build_request("GET", path, params=dict(params))
        response = self._send(request)
        try:
            self._raise_on_error(response)
            release = ProviderRelease(
                source_release=_bounded_release(response.headers.get("x-uniprot-release")),
                source_release_date=_bounded_release(
                    response.headers.get("x-uniprot-release-date")
                ),
            )
            body = self._read_bounded(response)
        finally:
            response.close()
        try:
            payload = json.loads(body)
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
        return payload, release

    def _send(self, request: httpx.Request) -> httpx.Response:
        try:
            return self._client.send(request, stream=True)
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

    def _read_bounded(self, response: httpx.Response) -> bytes:
        """Read at most `MAX_RESPONSE_BYTES` from an already-open stream.

        It deliberately does NOT close the response: the caller (`_get_json`) owns
        exactly one close in a `finally`, so ownership does not depend on which
        branch raised.
        """
        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
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
            # A redirect is how UniProt answers a secondary/merged accession. It
            # is an identity signal, never a transport detail: Phase 14 does not
            # follow it and does not adopt the redirect target as the requested
            # identity.
            raise self._error(
                CapabilityErrorKind.NOT_FOUND,
                "the UniProtKB accession is not an active primary accession; "
                "import the current active primary accession instead",
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
        # Messages are deliberately generic: no URL, no query text, no accession,
        # no upstream body, and no stack trace crosses this boundary.
        return CapabilityError(
            kind,
            message,
            provider_key=UNIPROT_PROVIDER_KEY,
            capability_kind=CapabilityKind.PROTEIN_DISCOVERY,
            retryable=retryable,
            upstream_status=upstream_status,
        )


class UniProtDriver:
    """UniProt provider driver: realizes protein discovery over the fixed REST host."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self.name = UNIPROT_PROVIDER_KEY
        self.display_name = UNIPROT_DISPLAY_NAME
        self.description: str | None = (
            "UniProt protein discovery over the official REST API (read-only "
            "protein records and canonical sequences)."
        )
        self.authorities: tuple[str, ...] = (UNIPROT_AUTHORITY,)
        # Phase 14 requires no credential: the public UniProt REST surface is
        # unauthenticated and there is no API-key feature in this slice.
        self.required_credential_kinds: tuple[str, ...] = ()
        self._transport = transport
        self._client: httpx.Client | None = None
        self._timeout = 15.0
        self.capabilities: Mapping[CapabilityKind, Any] = {}

    def start(self, context: DriverContext) -> None:
        self._timeout = float(context.settings.get("uniprot_timeout_seconds", 15.0))
        # Redirects are OFF: a `303` is exactly the inactive/secondary-accession
        # signal Phase 14 must fail closed on, and refusing to follow keeps the
        # fixed-host boundary absolute. `trust_env=False` makes the "no caller/
        # deployment proxy" rule literal: ambient HTTPS_PROXY/ALL_PROXY or
        # ~/.netrc must not silently reroute the fixed upstream.
        self._client = httpx.Client(
            base_url=UNIPROT_REST_BASE_URL,
            timeout=self._timeout,
            follow_redirects=False,
            trust_env=False,
            transport=self._transport,
        )
        self.capabilities = {
            CapabilityKind.PROTEIN_DISCOVERY: UniProtProteinDiscoveryCapability(self._client)
        }

    def stop(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        self.capabilities = {}

    def probe_health(self) -> ProviderRuntimeHealth:
        """Actor-independent runtime probe against the fixed host.

        A transport/decoding failure is `UNREACHABLE`; a non-2xx response is
        `DEGRADED` (the provider answered but unhealthy). The probe accepts no
        caller input, selects a single field, and reads at most
        `MAX_RESPONSE_BYTES`, so an unhealthy upstream can neither stream an
        unbounded body into memory nor influence the request shape. This never
        raises and never leaks an upstream body.
        """
        if self._client is None:
            return ProviderRuntimeHealth.UNREACHABLE
        try:
            request = self._client.build_request(
                "GET",
                _UNIPROTKB_SEARCH_PATH,
                params={
                    "query": _HEALTH_QUERY,
                    "format": "json",
                    "size": "1",
                    "fields": _HEALTH_FIELDS,
                },
                timeout=min(self._timeout, 10.0),
            )
            response = self._client.send(request, stream=True)
        except Exception:
            return ProviderRuntimeHealth.UNREACHABLE
        try:
            status = response.status_code
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    return ProviderRuntimeHealth.DEGRADED
        except Exception:
            return ProviderRuntimeHealth.UNREACHABLE
        finally:
            response.close()
        return (
            ProviderRuntimeHealth.READY
            if 200 <= status < 300
            else ProviderRuntimeHealth.DEGRADED
        )


__all__ = [
    "MAX_RESPONSE_BYTES",
    "UNIPROT_AUTHORITY",
    "UNIPROT_DISPLAY_NAME",
    "UNIPROT_PROVIDER_KEY",
    "UNIPROT_REST_BASE_URL",
    "ProviderRelease",
    "UniProtDriver",
]
