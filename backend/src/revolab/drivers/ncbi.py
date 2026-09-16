"""NCBI PubMed literature-discovery provider driver (Phase 13).

The only module allowed to know NCBI E-utilities vocabulary: its fixed host, its
`esearch.fcgi`/`esummary.fcgi` routes, its `tool`/`email` identification
parameters, its JSON response envelope, and its ESummary DocSum field names.
Everything above this module speaks the neutral `revolab.capabilities` value
objects (`LiteratureCandidate`, `LiteratureSearchResult`). Core never imports
this module.

Current-official-usage compliance (inspected 2026-09; see
`docs/architecture/EXTERNAL_LITERATURE_DISCOVERY.md` section 9):

* a FIXED upstream base URL (``https://eutils.ncbi.nlm.nih.gov/entrez/eutils/``);
  callers may never supply a URL, host, scheme, port, proxy, or HTTP method;
* the required application ``tool`` identifier and operator ``email`` contact are
  sent on every request and come from server configuration — never hard-coded;
* conservative no-key request pacing (NCBI: at most 3 requests/second/IP without
  an API key). Phase 13 has no API-key feature, so the default interval stays
  below that ceiling;
* one ESearch plus ONE batched ESummary per search — never one request per PMID;
* bounded query, bounded result count, bounded response bytes, bounded timeouts;
* read-only: no PDF/full-text fetch, no abstract persistence, no crawling.
"""

from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from collections.abc import Mapping
from typing import Any

import httpx

from revolab.capabilities import (
    MAX_LITERATURE_AUTHOR_CHARS,
    MAX_LITERATURE_AUTHORS,
    MAX_LITERATURE_DOI_CHARS,
    MAX_LITERATURE_JOURNAL_CHARS,
    MAX_LITERATURE_QUERY_CHARS,
    MAX_LITERATURE_RESULT_LIMIT,
    MAX_LITERATURE_TITLE_CHARS,
    CapabilityError,
    LiteratureCandidate,
    LiteratureSearchResult,
)
from revolab.credentials import CredentialLease
from revolab.drivers import DriverContext
from revolab.enums import CapabilityErrorKind, CapabilityKind, ProviderRuntimeHealth

NCBI_PROVIDER_KEY = "ncbi"
# The DURABLE identity namespace this driver resolves. It is deliberately NOT the
# provider/resolver key (`ncbi`): a future resolver could resolve the same
# `pubmed:<pmid>` identity without changing any stored LiteratureReference.
PUBMED_AUTHORITY = "pubmed"
NCBI_DISPLAY_NAME = "NCBI PubMed"

# The single fixed official E-utilities host. Never caller-configurable per
# request; the base URL is a process/deployment constant.
NCBI_EUTILS_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
_ESEARCH_PATH = "esearch.fcgi"
_ESUMMARY_PATH = "esummary.fcgi"
_EINFO_PATH = "einfo.fcgi"

# Response-byte ceiling. Checked WHILE streaming so an oversized upstream body is
# abandoned rather than buffered into memory.
MAX_RESPONSE_BYTES = 1_048_576  # 1 MiB

# PMIDs are positive decimal identifiers. Anything else is not a PubMed identity
# and fails closed rather than being forwarded to the provider or persisted.
_PMID_RE = re.compile(r"^[0-9]{1,12}$")
_YEAR_RE = re.compile(r"(?:^|\D)([12][0-9]{3})(?:\D|$)")

# Unicode categories that are never part of bounded presentation text: C0/C1
# controls, format characters, surrogates, private use, unassigned.
_STRIPPED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})


def _sanitize_pmid(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if _PMID_RE.fullmatch(text) else None


def _bounded_text(value: Any, limit: int) -> str | None:
    """Normalize one external text field into bounded inert presentation text.

    Control/format characters are removed (so provider text can never carry
    terminal escapes or invisible instruction-shaping characters), whitespace is
    collapsed, and the result is truncated to `limit`. Provider text stays
    UNTRUSTED data: it is never interpreted as markup, HTML, or instructions.
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


def _publication_year(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = _YEAR_RE.search(value)
    if match is None:
        return None
    year = int(match.group(1))
    return year if 1000 <= year <= 2999 else None


class _Pacer:
    """Process-local conservative request pacing for ONE driver instance.

    NCBI documents a hard no-key ceiling of 3 requests/second per IP and warns
    that abuse can block the IP. This enforces a minimum interval between
    requests, so a search (ESearch + one batched ESummary) is spaced instead of
    bursting. There is deliberately no background prefetch and no automatic
    retry: `search`/`resolve` issue exactly the requests they need.
    """

    def __init__(self, min_interval_seconds: float) -> None:
        self._interval = max(min_interval_seconds, 0.0)
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        if self._interval <= 0.0:
            return
        with self._lock:
            now = time.monotonic()
            delay = self._next_allowed - now
            if delay > 0:
                time.sleep(delay)
                now = time.monotonic()
            self._next_allowed = now + self._interval


class NCBILiteratureDiscoveryCapability:
    """`CapabilityKind.LITERATURE_DISCOVERY` realization over NCBI E-utilities."""

    provider_key = NCBI_PROVIDER_KEY
    kind = CapabilityKind.LITERATURE_DISCOVERY

    def __init__(
        self,
        client: httpx.Client,
        *,
        tool: str,
        email: str,
        pacer: _Pacer,
    ) -> None:
        self._client = client
        self._tool = tool
        self._email = email
        # The pacer is owned by the DRIVER so the health probe and every capability
        # call share ONE process-local request budget for the fixed upstream.
        self._pacer = pacer

    # -- discovery ----------------------------------------------------------------

    def search(
        self, query: str, limit: int, credentials: CredentialLease
    ) -> LiteratureSearchResult:
        """One bounded discovery search: ESearch, then ONE batched ESummary.

        `credentials` is accepted for protocol uniformity (the invocation layer
        always builds a lease) but Phase 13's provider is unauthenticated: no
        credential kind is required and none is sent on the wire.
        """
        bounded_query = self._bounded_query(query)
        bounded_limit = self._bounded_limit(limit)
        payload = self._get_json(
            _ESEARCH_PATH,
            {
                "db": "pubmed",
                "term": bounded_query,
                "retmax": str(bounded_limit),
                "sort": "relevance",
            },
        )
        result = payload.get("esearchresult")
        if not isinstance(result, Mapping):
            raise self._error(
                CapabilityErrorKind.UNKNOWN, "unexpected provider search envelope"
            )
        raw_ids = result.get("idlist")
        if not isinstance(raw_ids, list):
            raise self._error(
                CapabilityErrorKind.UNKNOWN, "unexpected provider identifier list"
            )
        pmids: list[str] = []
        for raw in raw_ids:
            if len(pmids) >= bounded_limit:
                break
            pmid = _sanitize_pmid(raw)
            if pmid is None:
                # A provider identifier that is not a PubMed identity is
                # structurally impossible data: fail closed rather than forward
                # or persist it.
                raise self._error(
                    CapabilityErrorKind.UNKNOWN,
                    "unexpected provider identifier in the search result",
                )
            if pmid not in pmids:
                pmids.append(pmid)
        if not pmids:
            return LiteratureSearchResult(provider_key=self.provider_key, candidates=())
        return LiteratureSearchResult(
            provider_key=self.provider_key,
            candidates=tuple(self._summaries(pmids, require_all=False)),
        )

    def resolve(
        self, authority: str, native_id: str, credentials: CredentialLease
    ) -> LiteratureCandidate:
        """Re-read ONE publication by its durable external identity.

        Nothing about the caller-supplied bibliographic data is trusted: the
        identity is re-resolved at the CURRENT provider before any durable write.
        """
        if authority != PUBMED_AUTHORITY:
            raise self._error(
                CapabilityErrorKind.INVALID_PARAM,
                "this provider resolves only the pubmed identity authority",
            )
        pmid = _sanitize_pmid(native_id)
        if pmid is None:
            raise self._error(
                CapabilityErrorKind.INVALID_PARAM, "invalid PubMed identifier"
            )
        candidates = self._summaries([pmid], require_all=True)
        candidate = candidates[0]
        # Defense-in-depth identity check: the provider must hand back the exact
        # durable identity that was requested.
        if candidate.authority != authority or candidate.native_id != pmid:
            raise self._error(
                CapabilityErrorKind.UNKNOWN,
                "provider returned a mismatched publication identity",
            )
        return candidate

    def _summaries(self, pmids: list[str], *, require_all: bool) -> list[LiteratureCandidate]:
        payload = self._get_json(
            _ESUMMARY_PATH, {"db": "pubmed", "id": ",".join(pmids)}
        )
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise self._error(
                CapabilityErrorKind.UNKNOWN, "unexpected provider summary envelope"
            )
        uids = result.get("uids")
        if not isinstance(uids, list):
            raise self._error(
                CapabilityErrorKind.UNKNOWN, "unexpected provider summary identifier list"
            )
        candidates: list[LiteratureCandidate] = []
        seen: set[str] = set()
        for raw in uids:
            if len(candidates) >= len(pmids):
                break
            uid = _sanitize_pmid(raw)
            if uid is None or uid in seen:
                raise self._error(
                    CapabilityErrorKind.UNKNOWN,
                    "unexpected provider identifier in the summary result",
                )
            seen.add(uid)
            docsum = result.get(uid)
            if not isinstance(docsum, Mapping) or self._is_error_docsum(docsum):
                continue
            candidates.append(self._normalize(uid, docsum))
        if require_all and not candidates:
            raise self._error(
                CapabilityErrorKind.NOT_FOUND, "publication not found at the provider"
            )
        return candidates

    @staticmethod
    def _is_error_docsum(docsum: Mapping[str, Any]) -> bool:
        """ESummary reports an unknown/withdrawn UID as an entry carrying an
        `error` field instead of a record."""
        return "error" in docsum and not docsum.get("title") and not docsum.get("pubdate")

    def _normalize(self, uid: str, docsum: Mapping[str, Any]) -> LiteratureCandidate:
        """Translate ONE ESummary DocSum into a bounded provider-neutral candidate.

        Provider-specific field names never leave this method. Oversized text is
        truncated to its neutral bound; a missing/invalid field becomes `None`
        rather than failing the whole search.
        """
        raw_authors = docsum.get("authors")
        authors: list[str] = []
        if isinstance(raw_authors, list):
            for entry in raw_authors:
                if len(authors) >= MAX_LITERATURE_AUTHORS:
                    break
                name = entry.get("name") if isinstance(entry, Mapping) else None
                bounded = _bounded_text(name, MAX_LITERATURE_AUTHOR_CHARS)
                if bounded is not None:
                    authors.append(bounded)
        journal = _bounded_text(
            docsum.get("fulljournalname") or docsum.get("source"),
            MAX_LITERATURE_JOURNAL_CHARS,
        )
        doi: str | None = None
        article_ids = docsum.get("articleids")
        if isinstance(article_ids, list):
            for entry in article_ids:
                if not isinstance(entry, Mapping):
                    continue
                if str(entry.get("idtype", "")).lower() == "doi":
                    doi = _bounded_text(entry.get("value"), MAX_LITERATURE_DOI_CHARS)
                    break
        return LiteratureCandidate(
            provider_key=self.provider_key,
            authority=PUBMED_AUTHORITY,
            native_id=uid,
            title=_bounded_text(docsum.get("title"), MAX_LITERATURE_TITLE_CHARS),
            authors=tuple(authors),
            journal=journal,
            publication_year=_publication_year(docsum.get("pubdate")),
            doi=doi,
        )

    # -- bounds -------------------------------------------------------------------

    @staticmethod
    def _bounded_query(query: str) -> str:
        if not isinstance(query, str):
            raise CapabilityError(
                CapabilityErrorKind.INVALID_PARAM,
                "search query must be text",
                provider_key=NCBI_PROVIDER_KEY,
                capability_kind=CapabilityKind.LITERATURE_DISCOVERY,
            )
        text = query.strip()
        if not text or len(text) > MAX_LITERATURE_QUERY_CHARS:
            raise CapabilityError(
                CapabilityErrorKind.INVALID_PARAM,
                "search query is empty or exceeds the supported length",
                provider_key=NCBI_PROVIDER_KEY,
                capability_kind=CapabilityKind.LITERATURE_DISCOVERY,
            )
        return text

    @staticmethod
    def _bounded_limit(limit: int) -> int:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise CapabilityError(
                CapabilityErrorKind.INVALID_PARAM,
                "result limit must be a positive integer",
                provider_key=NCBI_PROVIDER_KEY,
                capability_kind=CapabilityKind.LITERATURE_DISCOVERY,
            )
        return min(limit, MAX_LITERATURE_RESULT_LIMIT)

    # -- transport ----------------------------------------------------------------

    def _get_json(self, path: str, params: Mapping[str, str]) -> Mapping[str, Any]:
        """One bounded, paced, read-only GET against the FIXED E-utilities host.

        `path` is a module constant, never caller input, and the query is passed
        through httpx parameter encoding — caller text is never concatenated into
        a URL. Redirects are not followed.
        """
        self._pacer.wait()
        query = dict(params)
        query["tool"] = self._tool
        query["email"] = self._email
        query["retmode"] = "json"
        request = self._client.build_request("GET", path, params=query)
        response = self._send(request)
        self._raise_on_error(response)
        body = self._read_bounded(response)
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
        return payload

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
        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    response.close()
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
            response.close()
            raise self._error(
                CapabilityErrorKind.NETWORK,
                "the provider response could not be read",
                retryable=True,
            ) from exc
        except Exception as exc:
            response.close()
            raise self._error(
                CapabilityErrorKind.UNKNOWN, "the provider response could not be read"
            ) from exc
        finally:
            response.close()
        return b"".join(chunks)

    def _raise_on_error(self, response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if status in {401, 403}:
            raise self._error(
                CapabilityErrorKind.AUTH, "the provider rejected the request"
            )
        if status == 404:
            raise self._error(
                CapabilityErrorKind.NOT_FOUND, "the provider identity was not found"
            )
        if status in {400, 422}:
            raise self._error(
                CapabilityErrorKind.INVALID_PARAM, "the provider rejected the request parameters"
            )
        if status == 429 or status >= 500:
            raise self._error(
                CapabilityErrorKind.PROVIDER_UNAVAILABLE,
                "the provider is temporarily unavailable",
                retryable=True,
                upstream_status=status,
            )
        raise self._error(
            CapabilityErrorKind.UNKNOWN, "unexpected provider response status"
        )

    def _error(
        self,
        kind: CapabilityErrorKind,
        message: str,
        *,
        retryable: bool = False,
        upstream_status: int | None = None,
    ) -> CapabilityError:
        # Messages are deliberately generic: no URL, no query text, no upstream
        # body, no operator contact, and no stack trace crosses this boundary.
        return CapabilityError(
            kind,
            message,
            provider_key=NCBI_PROVIDER_KEY,
            capability_kind=CapabilityKind.LITERATURE_DISCOVERY,
            retryable=retryable,
            upstream_status=upstream_status,
        )


class NCBIDriver:
    """NCBI provider driver: realizes literature discovery over PubMed."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self.name = NCBI_PROVIDER_KEY
        self.display_name = NCBI_DISPLAY_NAME
        self.description: str | None = (
            "NCBI PubMed literature discovery over the official E-utilities "
            "(read-only citation metadata)."
        )
        self.authorities: tuple[str, ...] = (PUBMED_AUTHORITY,)
        # Phase 13 requires no credential: the first slice works within the
        # official unauthenticated E-utilities rate and has no API-key feature.
        self.required_credential_kinds: tuple[str, ...] = ()
        self._transport = transport
        self._client: httpx.Client | None = None
        self._tool = ""
        self._email = ""
        self._timeout = 10.0
        self._min_interval = 0.34
        # ONE process-local request budget shared by the health probe and every
        # capability call (the pacer is per-process, so a multi-worker deployment
        # paces independently per worker — documented in the architecture doc).
        self._pacer = _Pacer(0.0)
        self.capabilities: Mapping[CapabilityKind, Any] = {}

    def start(self, context: DriverContext) -> None:
        tool = str(context.settings.get("ncbi_tool") or "").strip()
        email = str(context.settings.get("ncbi_email") or "").strip()
        # NCBI requires BOTH on every request. A partially configured deployment
        # fails loudly (the lifespan propagates it) instead of probing anonymously
        # and risking an IP block.
        if not tool or not email:
            raise RuntimeError(
                "NCBI driver requires the 'ncbi_tool' and 'ncbi_email' settings"
            )
        self._tool = tool
        self._email = email
        self._timeout = float(context.settings.get("ncbi_timeout_seconds", 10.0))
        self._min_interval = float(
            context.settings.get("ncbi_min_request_interval_seconds", 0.34)
        )
        self._pacer = _Pacer(self._min_interval)
        # Redirects are OFF: the E-utilities host never needs one, and refusing to
        # follow keeps the fixed-host boundary absolute. `trust_env=False` makes the
        # "no caller/deployment proxy" rule literal: ambient HTTPS_PROXY/ALL_PROXY
        # or ~/.netrc must not silently reroute the fixed upstream.
        self._client = httpx.Client(
            base_url=NCBI_EUTILS_BASE_URL,
            timeout=self._timeout,
            follow_redirects=False,
            trust_env=False,
            transport=self._transport,
        )
        self.capabilities = {
            CapabilityKind.LITERATURE_DISCOVERY: NCBILiteratureDiscoveryCapability(
                self._client,
                tool=self._tool,
                email=self._email,
                pacer=self._pacer,
            )
        }

    def stop(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        self.capabilities = {}

    def probe_health(self) -> ProviderRuntimeHealth:
        """Actor-independent runtime probe against the fixed host.

        A transport/decoding failure is `UNREACHABLE`; a non-2xx response is
        `DEGRADED` (the provider answered but unhealthy). The probe is paced like
        every other request and reads at most `MAX_RESPONSE_BYTES`, so an unhealthy
        upstream can neither exhaust the request budget nor stream an unbounded body
        into memory. This never raises and never leaks an upstream body.
        """
        if self._client is None:
            return ProviderRuntimeHealth.UNREACHABLE
        try:
            self._pacer.wait()
            request = self._client.build_request(
                "GET",
                _EINFO_PATH,
                params={
                    "db": "pubmed",
                    "retmode": "json",
                    "tool": self._tool,
                    "email": self._email,
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
    "NCBI_DISPLAY_NAME",
    "NCBI_EUTILS_BASE_URL",
    "NCBI_PROVIDER_KEY",
    "PUBMED_AUTHORITY",
    "NCBIDriver",
]
