"""Deterministic NCBI PubMed driver tests over an HTTP fake at the network boundary.

CI never touches live NCBI (TODO.md section 35/59). The fake stands in for the
official E-utilities HTTP surface; the driver translation itself is exercised for
real (no mocking of the driver). Canned payloads are synthetic citation metadata
only — no copyrighted abstract or full-text fixture is committed.
"""

from __future__ import annotations

import dataclasses

import httpx
import pytest

from revolab.capabilities import (
    MAX_LITERATURE_RESULT_LIMIT,
    MAX_LITERATURE_TITLE_CHARS,
    CapabilityError,
    LiteratureCandidate,
    LiteratureSearchResult,
)
from revolab.credentials import CredentialLease
from revolab.drivers import DriverContext
from revolab.drivers.ncbi import (
    NCBI_EUTILS_BASE_URL,
    NCBI_PROVIDER_KEY,
    PUBMED_AUTHORITY,
    NCBIDriver,
)
from revolab.enums import CapabilityErrorKind, CapabilityKind, ProviderRuntimeHealth
from revolab.schemas import LiteratureCandidateRead

TOOL = "revolab-test"
EMAIL = "operator@example.test"


def _lease() -> CredentialLease:
    # Phase 13's provider is keyless; the invocation layer still always builds a
    # (possibly empty) lease.
    return CredentialLease({})


def _driver(handler) -> NCBIDriver:
    driver = NCBIDriver(transport=httpx.MockTransport(handler))
    driver.start(
        DriverContext(
            environment="test",
            settings={
                "ncbi_tool": TOOL,
                "ncbi_email": EMAIL,
                "ncbi_timeout_seconds": 5.0,
                # Tests must not sleep; production default stays sub-3 req/s.
                "ncbi_min_request_interval_seconds": 0.0,
            },
        )
    )
    return driver


def _capability(driver: NCBIDriver):
    return driver.capabilities[CapabilityKind.LITERATURE_DISCOVERY]


def _esearch_payload(ids: list[str]) -> dict:
    return {
        "header": {"type": "esearch", "version": "0.3"},
        "esearchresult": {
            "count": str(len(ids)),
            "retmax": str(len(ids)),
            "retstart": "0",
            "idlist": list(ids),
            "translationset": [],
            "querytranslation": "synthetic",
        },
    }


def _docsum(uid: str, **overrides) -> dict:
    base = {
        "uid": uid,
        "pubdate": "2021 Jan 15",
        "title": f"Synthetic title {uid}",
        "authors": [{"name": "Ada Lovelace", "authtype": "Author"}],
        "fulljournalname": "Journal of Synthetic Records",
        "source": "J Synth Rec",
        "articleids": [
            {"idtype": "pubmed", "value": uid},
            {"idtype": "doi", "value": f"10.1000/{uid}"},
        ],
    }
    base.update(overrides)
    return base


def _esummary_payload(docsums: list[dict]) -> dict:
    result: dict = {"uids": [str(d["uid"]) for d in docsums]}
    for docsum in docsums:
        result[str(docsum["uid"])] = docsum
    return {"header": {"type": "esummary", "version": "0.3"}, "result": result}


def _json_response(payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _by_path(handler_map: dict[str, object]):
    """Route a request by its E-utilities path."""

    def handler(request: httpx.Request) -> httpx.Response:
        for suffix, responder in handler_map.items():
            if request.url.path.endswith(suffix):
                assert callable(responder)
                return responder(request)
        raise AssertionError(f"unexpected E-utilities path: {request.url.path}")

    return handler


# ---------------------------------------------------------------------------
# Query construction, identification, and batching
# ---------------------------------------------------------------------------


def test_search_uses_fixed_host_and_registered_identification() -> None:
    seen: list[httpx.Request] = []

    def esearch(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _json_response(_esearch_payload(["111"]))

    def esummary(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _json_response(_esummary_payload([_docsum("111")]))

    driver = _driver(_by_path({"esearch.fcgi": esearch, "esummary.fcgi": esummary}))
    result = _capability(driver).search('enzyme "active site" redesign & more', 5, _lease())

    assert isinstance(result, LiteratureSearchResult)
    assert len(seen) == 2
    for request in seen:
        # Fixed official host only — never caller-controlled.
        assert str(request.url).startswith(NCBI_EUTILS_BASE_URL)
        assert request.url.host == "eutils.ncbi.nlm.nih.gov"
        assert request.method == "GET"
        # Required NCBI identification is present on EVERY request.
        assert request.url.params["tool"] == TOOL
        assert request.url.params["email"] == EMAIL
        assert request.url.params["retmode"] == "json"
    # The caller's opaque query text round-trips as ONE encoded parameter value.
    assert seen[0].url.params["term"] == 'enzyme "active site" redesign & more'
    assert seen[0].url.params["db"] == "pubmed"
    assert seen[1].url.params["db"] == "pubmed"


def test_search_batches_pmids_into_one_summary_request() -> None:
    summary_calls: list[str] = []

    def esearch(request: httpx.Request) -> httpx.Response:
        return _json_response(_esearch_payload(["111", "222", "333"]))

    def esummary(request: httpx.Request) -> httpx.Response:
        summary_calls.append(request.url.params["id"])
        return _json_response(
            _esummary_payload([_docsum("111"), _docsum("222"), _docsum("333")])
        )

    driver = _driver(_by_path({"esearch.fcgi": esearch, "esummary.fcgi": esummary}))
    result = _capability(driver).search("anything", 3, _lease())

    # ONE batched request, never one request per PMID.
    assert summary_calls == ["111,222,333"]
    assert [candidate.native_id for candidate in result.candidates] == ["111", "222", "333"]


def test_search_normalizes_provider_metadata_into_neutral_candidate() -> None:
    def esearch(request: httpx.Request) -> httpx.Response:
        return _json_response(_esearch_payload(["111"]))

    def esummary(request: httpx.Request) -> httpx.Response:
        return _json_response(_esummary_payload([_docsum("111")]))

    driver = _driver(_by_path({"esearch.fcgi": esearch, "esummary.fcgi": esummary}))
    candidate = _capability(driver).search("x", 5, _lease()).candidates[0]

    assert candidate.provider_key == NCBI_PROVIDER_KEY
    assert candidate.authority == PUBMED_AUTHORITY
    assert candidate.native_id == "111"
    assert candidate.title == "Synthetic title 111"
    assert candidate.authors == ("Ada Lovelace",)
    assert candidate.journal == "Journal of Synthetic Records"
    assert candidate.publication_year == 2021
    assert candidate.doi == "10.1000/111"


def test_provider_key_is_not_the_durable_authority() -> None:
    """TODO.md section 5: `ncbi` (resolver) and `pubmed` (authority) differ."""
    assert NCBI_PROVIDER_KEY == "ncbi"
    assert PUBMED_AUTHORITY == "pubmed"
    assert NCBI_PROVIDER_KEY != PUBMED_AUTHORITY
    driver = _driver(_by_path({}))
    assert driver.authorities == (PUBMED_AUTHORITY,)
    assert NCBI_PROVIDER_KEY not in driver.authorities
    assert NCBI_PROVIDER_KEY not in {kind.value for kind in CapabilityKind}


def test_provider_specific_fields_do_not_leak_past_the_driver() -> None:
    """TODO.md section 58.2: ESummary field names never leave the driver."""
    neutral = {
        "provider_key",
        "authority",
        "native_id",
        "title",
        "authors",
        "journal",
        "publication_year",
        "doi",
    }
    assert set(LiteratureCandidate.__dataclass_fields__) == neutral
    assert set(LiteratureCandidateRead.model_fields) == neutral

    def esearch(request: httpx.Request) -> httpx.Response:
        return _json_response(_esearch_payload(["111"]))

    def esummary(request: httpx.Request) -> httpx.Response:
        return _json_response(_esummary_payload([_docsum("111")]))

    driver = _driver(_by_path({"esearch.fcgi": esearch, "esummary.fcgi": esummary}))
    candidate = _capability(driver).search("x", 5, _lease()).candidates[0]
    dumped = dataclasses.asdict(candidate)
    for provider_field in ("pubdate", "fulljournalname", "source", "articleids", "uid"):
        assert provider_field not in dumped


# ---------------------------------------------------------------------------
# Bounds: query / limit / result count / response bytes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", ["", "   ", "x" * 301])
def test_over_bound_query_fails_closed_without_a_request(query: str) -> None:
    calls: list[httpx.Request] = []
    driver = _driver(_by_path({"esearch.fcgi": lambda r: calls.append(r) or _json_response({})}))
    with pytest.raises(CapabilityError) as excinfo:
        _capability(driver).search(query, 5, _lease())
    assert excinfo.value.kind is CapabilityErrorKind.INVALID_PARAM
    assert calls == []


@pytest.mark.parametrize("limit", [0, -1, True, "5"])
def test_invalid_limit_fails_closed(limit) -> None:
    driver = _driver(_by_path({}))
    with pytest.raises(CapabilityError) as excinfo:
        _capability(driver).search("x", limit, _lease())
    assert excinfo.value.kind is CapabilityErrorKind.INVALID_PARAM


def test_result_limit_is_clamped_to_the_canonical_ceiling() -> None:
    seen: dict[str, str] = {}

    def esearch(request: httpx.Request) -> httpx.Response:
        seen["retmax"] = request.url.params["retmax"]
        return _json_response(_esearch_payload([]))

    driver = _driver(_by_path({"esearch.fcgi": esearch}))
    _capability(driver).search("x", 1000, _lease())
    assert seen["retmax"] == str(MAX_LITERATURE_RESULT_LIMIT)


def test_empty_search_returns_no_candidates_and_makes_no_summary_call() -> None:
    summary_calls: list[httpx.Request] = []

    driver = _driver(
        _by_path(
            {
                "esearch.fcgi": lambda r: _json_response(_esearch_payload([])),
                "esummary.fcgi": lambda r: summary_calls.append(r) or _json_response({}),
            }
        )
    )
    result = _capability(driver).search("nothing matches", 5, _lease())
    assert result.candidates == ()
    assert summary_calls == []


def test_oversized_response_fails_closed() -> None:
    huge = b"x" * (1_100_000)

    def esearch(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=huge, headers={"content-type": "application/json"})

    driver = _driver(_by_path({"esearch.fcgi": esearch}))
    with pytest.raises(CapabilityError) as excinfo:
        _capability(driver).search("x", 5, _lease())
    assert excinfo.value.kind is CapabilityErrorKind.PROVIDER_UNAVAILABLE


# ---------------------------------------------------------------------------
# Malformed / unexpected provider payloads fail closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"esearchresult": "nope"},
        {"esearchresult": {"idlist": "nope"}},
        {"esearchresult": {"idlist": ["not-a-pmid"]}},
        {"esearchresult": {"idlist": [123]}},
    ],
)
def test_malformed_search_payload_fails_closed(payload: dict) -> None:
    driver = _driver(_by_path({"esearch.fcgi": lambda r: _json_response(payload)}))
    with pytest.raises(CapabilityError) as excinfo:
        _capability(driver).search("x", 5, _lease())
    assert excinfo.value.kind is CapabilityErrorKind.UNKNOWN


def test_non_json_payload_fails_closed() -> None:
    driver = _driver(
        _by_path(
            {"esearch.fcgi": lambda r: httpx.Response(200, content=b"<xml>not json</xml>")}
        )
    )
    with pytest.raises(CapabilityError) as excinfo:
        _capability(driver).search("x", 5, _lease())
    assert excinfo.value.kind is CapabilityErrorKind.UNKNOWN


@pytest.mark.parametrize(
    "payload",
    [{}, {"result": "nope"}, {"result": {"uids": "nope"}}, {"result": {"uids": [123]}}],
)
def test_malformed_summary_payload_fails_closed(payload: dict) -> None:
    driver = _driver(
        _by_path(
            {
                "esearch.fcgi": lambda r: _json_response(_esearch_payload(["111"])),
                "esummary.fcgi": lambda r: _json_response(payload),
            }
        )
    )
    with pytest.raises(CapabilityError) as excinfo:
        _capability(driver).search("x", 5, _lease())
    assert excinfo.value.kind is CapabilityErrorKind.UNKNOWN


def test_unexpected_docsum_fields_are_ignored_and_missing_ones_are_none() -> None:
    docsum = {
        "uid": "111",
        "some_future_field": {"nested": True},
        "authors": [],
        "articleids": [],
    }
    driver = _driver(
        _by_path(
            {
                "esearch.fcgi": lambda r: _json_response(_esearch_payload(["111"])),
                "esummary.fcgi": lambda r: _json_response(_esummary_payload([docsum])),
            }
        )
    )
    candidate = _capability(driver).search("x", 5, _lease()).candidates[0]
    assert candidate.title is None
    assert candidate.authors == ()
    assert candidate.journal is None
    assert candidate.publication_year is None
    assert candidate.doi is None


def test_long_title_is_bounded_and_authors_are_capped() -> None:
    docsum = _docsum(
        "111",
        title="T" * 5_000,
        authors=[{"name": "A" * 1_000} for _ in range(100)],
        fulljournalname="J" * 1_000,
        articleids=[{"idtype": "doi", "value": "D" * 1_000}],
    )
    driver = _driver(
        _by_path(
            {
                "esearch.fcgi": lambda r: _json_response(_esearch_payload(["111"])),
                "esummary.fcgi": lambda r: _json_response(_esummary_payload([docsum])),
            }
        )
    )
    candidate = _capability(driver).search("x", 5, _lease()).candidates[0]
    assert candidate.title is not None and len(candidate.title) == MAX_LITERATURE_TITLE_CHARS
    assert len(candidate.authors) == 20
    assert all(len(author) <= 200 for author in candidate.authors)
    assert candidate.journal is not None and len(candidate.journal) == 200
    assert candidate.doi is not None and len(candidate.doi) == 200


def test_control_characters_are_stripped_from_presentation_text() -> None:
    docsum = _docsum("111", title="Safe\x00\x1b[31m\ntext\u202e")
    driver = _driver(
        _by_path(
            {
                "esearch.fcgi": lambda r: _json_response(_esearch_payload(["111"])),
                "esummary.fcgi": lambda r: _json_response(_esummary_payload([docsum])),
            }
        )
    )
    candidate = _capability(driver).search("x", 5, _lease()).candidates[0]
    assert candidate.title == "Safe [31m text"


# ---------------------------------------------------------------------------
# resolve: CURRENT provider re-resolution of the durable identity
# ---------------------------------------------------------------------------


def test_resolve_reads_one_publication_by_identity() -> None:
    seen: list[str] = []

    def esummary(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["id"])
        return _json_response(_esummary_payload([_docsum("12345678")]))

    driver = _driver(_by_path({"esummary.fcgi": esummary}))
    candidate = _capability(driver).resolve(PUBMED_AUTHORITY, "12345678", _lease())
    assert seen == ["12345678"]
    assert candidate.native_id == "12345678"
    assert candidate.authority == PUBMED_AUTHORITY


@pytest.mark.parametrize("authority", ["ncbi", "doi", "uniprot", ""])
def test_resolve_rejects_a_foreign_authority(authority: str) -> None:
    driver = _driver(_by_path({}))
    with pytest.raises(CapabilityError) as excinfo:
        _capability(driver).resolve(authority, "12345678", _lease())
    assert excinfo.value.kind is CapabilityErrorKind.INVALID_PARAM


@pytest.mark.parametrize("native_id", ["", "abc", "12.5", "1234567890123456", "-1"])
def test_resolve_rejects_a_malformed_identifier(native_id: str) -> None:
    driver = _driver(_by_path({}))
    with pytest.raises(CapabilityError) as excinfo:
        _capability(driver).resolve(PUBMED_AUTHORITY, native_id, _lease())
    assert excinfo.value.kind is CapabilityErrorKind.INVALID_PARAM


def test_resolve_identity_mismatch_fails_closed() -> None:
    driver = _driver(
        _by_path(
            {
                "esummary.fcgi": lambda r: _json_response(
                    _esummary_payload([_docsum("99999999")])
                )
            }
        )
    )
    with pytest.raises(CapabilityError) as excinfo:
        _capability(driver).resolve(PUBMED_AUTHORITY, "12345678", _lease())
    assert excinfo.value.kind is CapabilityErrorKind.UNKNOWN


def test_resolve_unknown_publication_is_not_found() -> None:
    payload = {
        "header": {"type": "esummary"},
        "result": {
            "uids": ["12345678"],
            "12345678": {"uid": "12345678", "error": "cannot get document summary"},
        },
    }
    driver = _driver(_by_path({"esummary.fcgi": lambda r: _json_response(payload)}))
    with pytest.raises(CapabilityError) as excinfo:
        _capability(driver).resolve(PUBMED_AUTHORITY, "12345678", _lease())
    assert excinfo.value.kind is CapabilityErrorKind.NOT_FOUND


# ---------------------------------------------------------------------------
# Transport / HTTP failure mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, CapabilityErrorKind.AUTH),
        (403, CapabilityErrorKind.AUTH),
        (404, CapabilityErrorKind.NOT_FOUND),
        (400, CapabilityErrorKind.INVALID_PARAM),
        (422, CapabilityErrorKind.INVALID_PARAM),
        (429, CapabilityErrorKind.PROVIDER_UNAVAILABLE),
        (500, CapabilityErrorKind.PROVIDER_UNAVAILABLE),
        (502, CapabilityErrorKind.PROVIDER_UNAVAILABLE),
        (503, CapabilityErrorKind.PROVIDER_UNAVAILABLE),
        (418, CapabilityErrorKind.UNKNOWN),
    ],
)
def test_http_status_maps_to_a_typed_capability_error(status: int, expected) -> None:
    driver = _driver(_by_path({"esearch.fcgi": lambda r: httpx.Response(status, content=b"")}))
    with pytest.raises(CapabilityError) as excinfo:
        _capability(driver).search("x", 5, _lease())
    assert excinfo.value.kind is expected


def test_read_timeout_becomes_a_typed_network_failure() -> None:
    def esearch(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    driver = _driver(_by_path({"esearch.fcgi": esearch}))
    with pytest.raises(CapabilityError) as excinfo:
        _capability(driver).search("x", 5, _lease())
    assert excinfo.value.kind is CapabilityErrorKind.NETWORK
    assert excinfo.value.retryable is True


def test_connect_error_becomes_a_typed_network_failure() -> None:
    def esearch(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("synthetic connect failure", request=request)

    driver = _driver(_by_path({"esearch.fcgi": esearch}))
    with pytest.raises(CapabilityError) as excinfo:
        _capability(driver).search("x", 5, _lease())
    assert excinfo.value.kind is CapabilityErrorKind.NETWORK


def test_error_messages_never_leak_operator_contact_query_or_upstream_body() -> None:
    sentinel_query = "SENTINEL_QUERY_aaaabbbbccc"
    sentinel_body = "SENTINEL_UPSTREAM_BODY_ddddeeeeffff"

    def esearch(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=sentinel_body.encode())

    driver = _driver(_by_path({"esearch.fcgi": esearch}))
    with pytest.raises(CapabilityError) as excinfo:
        _capability(driver).search(sentinel_query, 5, _lease())
    message = str(excinfo.value)
    assert sentinel_query not in message
    assert sentinel_body not in message
    assert EMAIL not in message
    assert "eutils.ncbi.nlm.nih.gov" not in message
    assert sentinel_query not in repr(excinfo.value)


def test_redirects_are_not_followed() -> None:
    """The fixed-host boundary is absolute: an upstream redirect is an error."""
    def esearch(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"location": "https://evil.example.test/collect"}
        )

    driver = _driver(_by_path({"esearch.fcgi": esearch}))
    with pytest.raises(CapabilityError) as excinfo:
        _capability(driver).search("x", 5, _lease())
    assert excinfo.value.kind is CapabilityErrorKind.UNKNOWN


# ---------------------------------------------------------------------------
# Driver lifecycle / configuration / health
# ---------------------------------------------------------------------------


def test_start_requires_operator_identity_and_contact() -> None:
    driver = NCBIDriver(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(RuntimeError):
        driver.start(DriverContext(environment="test", settings={"ncbi_tool": TOOL}))
    with pytest.raises(RuntimeError):
        driver.start(DriverContext(environment="test", settings={"ncbi_email": EMAIL}))
    with pytest.raises(RuntimeError):
        driver.start(DriverContext(environment="test", settings={}))


def test_driver_declares_no_required_credential_and_the_pubmed_authority() -> None:
    driver = NCBIDriver()
    assert driver.name == "ncbi"
    assert driver.display_name == "NCBI PubMed"
    assert driver.required_credential_kinds == ()
    assert driver.authorities == ("pubmed",)


def test_health_probe_maps_transport_and_status() -> None:
    driver = _driver(_by_path({"einfo.fcgi": lambda r: _json_response({"ok": True})}))
    assert driver.probe_health() is ProviderRuntimeHealth.READY

    driver = _driver(_by_path({"einfo.fcgi": lambda r: httpx.Response(503, content=b"")}))
    assert driver.probe_health() is ProviderRuntimeHealth.DEGRADED

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    driver = _driver(_by_path({"einfo.fcgi": boom}))
    assert driver.probe_health() is ProviderRuntimeHealth.UNREACHABLE


def test_stop_releases_the_transport() -> None:
    driver = _driver(_by_path({}))
    driver.stop()
    assert driver.capabilities == {}
    assert driver.probe_health() is ProviderRuntimeHealth.UNREACHABLE


# ---------------------------------------------------------------------------
# Rate pacing (documented no-key policy)
# ---------------------------------------------------------------------------


def test_pacer_enforces_the_minimum_interval(monkeypatch) -> None:
    from revolab.drivers import ncbi

    clock = {"now": 100.0}
    slept: list[float] = []

    monkeypatch.setattr(ncbi.time, "monotonic", lambda: clock["now"])

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(ncbi.time, "sleep", fake_sleep)

    pacer = ncbi._Pacer(0.5)
    pacer.wait()  # first request: no wait
    pacer.wait()  # spaced
    pacer.wait()  # spaced
    assert slept == [0.5, 0.5]


def test_pacer_is_disabled_for_a_zero_interval(monkeypatch) -> None:
    from revolab.drivers import ncbi

    slept: list[float] = []
    monkeypatch.setattr(ncbi.time, "sleep", lambda seconds: slept.append(seconds))
    ncbi._Pacer(0.0).wait()
    assert slept == []


def test_default_no_key_interval_stays_below_the_documented_ceiling() -> None:
    from revolab.config import Settings

    # NCBI documents at most 3 requests/second without an API key. A larger
    # default interval is strictly more conservative.
    interval = Settings().ncbi_min_request_interval_seconds
    assert interval >= 1 / 3
