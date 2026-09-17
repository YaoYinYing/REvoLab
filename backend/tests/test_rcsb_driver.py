"""Deterministic RCSB PDB driver tests over an HTTP fake at the network boundary.

CI never touches live RCSB (TODO.md sections 8/66/87). The fake stands in for the
three official hosts; the driver translation itself is exercised for real (no
mocking of the driver). Canned payloads are hand-written synthetic records that
mirror the CURRENT official Search API / Data API shapes and the synthetic
coordinate file mirrors the official PDBx/mmCIF data-block shape, all inspected on
2026-09-17 (`docs/architecture/EXTERNAL_STRUCTURE_IMPORT.md` section 9). No real
RCSB payload corpus is committed.
"""

from __future__ import annotations

import json

import httpx
import pytest

from revolab.capabilities import (
    MAX_STRUCTURE_METHODS,
    MAX_STRUCTURE_QUERY_CHARS,
    MAX_STRUCTURE_RESULT_LIMIT,
    STRUCTURE_COORDINATE_FORMAT,
    CapabilityError,
    StructureSearchResult,
)
from revolab.credentials import CredentialLease
from revolab.drivers import DriverContext
from revolab.drivers import rcsb as rcsb_module
from revolab.drivers.rcsb import (
    MAX_API_RESPONSE_BYTES,
    RCSB_AUTHORITY,
    RCSB_DATA_BASE_URL,
    RCSB_FILES_BASE_URL,
    RCSB_PROVIDER_KEY,
    RCSB_SEARCH_BASE_URL,
    RcsbDriver,
    canonical_entry_id,
    extended_alias_of_legacy,
    is_computed_model_id,
    same_entry_identity,
)
from revolab.enums import CapabilityErrorKind, CapabilityKind, ProviderRuntimeHealth

SEARCH_PATH = "/rcsbsearch/v2/query"
GRAPHQL_PATH = "/graphql"
COORDINATE_PATH = "/download/1abc.cif"
COORDINATE_TEXT = (
    "data_1ABC\n#\n_entry.id 1ABC\n_struct.title Synthetic\n"
    "_refine.ls_d_res_high 1.50\n#\n"
)


def _lease() -> CredentialLease:
    # Phase 15's provider is keyless; the invocation layer still always builds a
    # (possibly empty) lease.
    return CredentialLease({})


def _coordinate_response(text: str = COORDINATE_TEXT, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, content=text.encode(), headers={"content-type": "chemical/x-cif"}
    )


def _gql_entry(
    rcsb_id: str = "1ABC",
    *,
    title: object = "Synthetic structure",
    methods: object = ("X-RAY DIFFRACTION",),
    resolution: object = (1.5,),
    methodology: object = "experimental",
    polymer_entity_count: object = 2,
    major: object = 3,
    minor: object = 1,
    revision_date: object = "2026-08-12T00:00:00Z",
    release_date: object = "1984-07-17T00:00:00Z",
    extra: dict | None = None,
) -> dict:
    entry: dict = {
        "rcsb_id": rcsb_id,
        "struct": {"title": title},
        "exptl": [{"method": method} for method in methods] if isinstance(methods, (list, tuple)) else methods,
        "rcsb_entry_info": {
            "resolution_combined": resolution,
            "polymer_entity_count": polymer_entity_count,
            "structure_determination_methodology": methodology,
        },
        "rcsb_accession_info": {
            "initial_release_date": release_date,
            "major_revision": major,
            "minor_revision": minor,
            "revision_date": revision_date,
        },
    }
    if extra:
        entry.update(extra)
    return entry


def _gql(*entries: dict) -> httpx.Response:
    return httpx.Response(200, json={"data": {"entries": list(entries)}})


def _search(*identifiers: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "query_id": "q",
            "result_type": "entry",
            "total_count": len(identifiers),
            "result_set": [{"identifier": i, "score": 1.0} for i in identifiers],
        },
    )


class _Router:
    """Dispatches by (host, path) and records every outbound request."""

    def __init__(self, *, search=None, graphql=None, files=None) -> None:
        self.requests: list[httpx.Request] = []
        self._handlers = {
            ("search.rcsb.org", SEARCH_PATH): search,
            ("data.rcsb.org", GRAPHQL_PATH): graphql,
            ("files.rcsb.org", None): files,
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        handler = self._handlers.get((host, request.url.path))
        if handler is None:
            handler = self._handlers.get((host, None))
        if handler is None:
            raise AssertionError(f"unexpected request: {request.method} {request.url}")
        return handler(request)

    def of(self, host: str) -> list[httpx.Request]:
        return [request for request in self.requests if request.url.host == host]


def _driver(router: _Router, **settings) -> RcsbDriver:
    driver = RcsbDriver(transport=httpx.MockTransport(router))
    merged = {"rcsb_timeout_seconds": 5.0}
    merged.update(settings)
    driver.start(DriverContext(environment="test", settings=merged))
    return driver


def _capability(driver: RcsbDriver):
    return driver.capabilities[CapabilityKind.STRUCTURE_DISCOVERY]


def _search_body(request: httpx.Request) -> dict:
    return json.loads(request.content)


def _gql_variables(request: httpx.Request) -> dict:
    return json.loads(request.content)["variables"]


# ---------------------------------------------------------------------------
# identifier grammar / alias semantics
# ---------------------------------------------------------------------------


def test_legacy_and_extended_identifiers_are_the_same_entry() -> None:
    assert canonical_entry_id("1abc") == "1ABC"
    assert canonical_entry_id("1ABC") == "1ABC"
    assert canonical_entry_id("pdb_00001abc") == "pdb_00001abc"
    assert canonical_entry_id("PDB_00001ABC") == "pdb_00001abc"
    assert extended_alias_of_legacy("1ABC") == "pdb_00001abc"
    assert same_entry_identity("1ABC", "1abc")
    assert same_entry_identity("1abc", "pdb_00001abc")
    assert same_entry_identity("pdb_00001abc", "1ABC")
    assert not same_entry_identity("1ABC", "2XYZ")


@pytest.mark.parametrize(
    "value",
    ["", "1AB", "1ABCD", "ABC1", "pdb_0001abc", "pdb_00001abcD", "1a-b", "pdb_1abc", None, 7],
)
def test_malformed_identifiers_are_not_pdb_entry_ids(value: object) -> None:
    assert canonical_entry_id(value) is None


def test_extended_identifiers_are_not_assumed_to_be_four_characters() -> None:
    # A future extended-only identifier (no legacy alias) is grammar-valid, so
    # Phase 15 never rejects it merely for not being four characters.
    assert canonical_entry_id("pdb_1abc5678") == "pdb_1abc5678"
    assert not same_entry_identity("pdb_1abc5678", "1abc")


def test_computed_model_identifiers_are_recognized() -> None:
    assert is_computed_model_id("AF_AFB0M2T2F1")
    assert is_computed_model_id("MA_3J3Q")
    assert not is_computed_model_id("1ABC")
    assert not is_computed_model_id("pdb_00001abc")


# ---------------------------------------------------------------------------
# search: fixed hosts, bounded query translation, batched metadata
# ---------------------------------------------------------------------------


def test_search_translates_plain_text_into_one_bounded_search_request() -> None:
    router = _Router(search=lambda r: _search("1ABC"), graphql=lambda r: _gql(_gql_entry()))
    driver = _driver(router)
    result = _capability(driver).search("human hemoglobin", 5, _lease())

    assert isinstance(result, StructureSearchResult)
    assert result.provider_key == RCSB_PROVIDER_KEY
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.authority == RCSB_AUTHORITY
    assert candidate.native_id == "1ABC"
    assert candidate.title == "Synthetic structure"
    assert candidate.experimental_methods == ("X-RAY DIFFRACTION",)
    assert candidate.resolution_angstrom == 1.5
    assert candidate.polymer_entity_count == 2
    assert candidate.release_date == "1984-07-17T00:00:00Z"

    # exactly ONE search request and ONE batched metadata request (never N+1).
    assert len(router.of("search.rcsb.org")) == 1
    assert len(router.of("data.rcsb.org")) == 1

    body = _search_body(router.of("search.rcsb.org")[0])
    assert router.of("search.rcsb.org")[0].url.scheme == "https"
    assert body["return_type"] == "entry"
    assert body["request_options"]["paginate"] == {"start": 0, "rows": 5}
    assert body["request_options"]["results_content_type"] == ["experimental"]
    nodes = body["query"]["nodes"]
    full_text = [n for n in nodes if n["service"] == "full_text"]
    assert full_text and full_text[0]["parameters"] == {"value": "human hemoglobin"}
    # The `full_text` service has NO attribute parameter: putting the query in
    # `value` is exactly how a raw provider DSL is kept out of the caller's hands.
    assert "attribute" not in full_text[0]["parameters"]
    methodology = [n for n in nodes if n["service"] == "text"]
    assert methodology[0]["parameters"]["attribute"] == (
        "rcsb_entry_info.structure_determination_methodology"
    )
    assert methodology[0]["parameters"]["operator"] == "exact_match"
    assert methodology[0]["parameters"]["value"] == "experimental"

    # entry ids travel as BOUND GraphQL VARIABLES, never interpolated text.
    metadata_request = router.of("data.rcsb.org")[0]
    assert _gql_variables(metadata_request) == {"entry_ids": ["1ABC"]}
    assert "1ABC" not in json.loads(metadata_request.content)["query"]


def test_search_bounds_the_hit_list_to_the_requested_limit() -> None:
    router = _Router(
        search=lambda r: _search("1ABC", "2XYZ", "3QQQ"),
        graphql=lambda r: _gql(_gql_entry("1ABC"), _gql_entry("2XYZ"), _gql_entry("3QQQ")),
    )
    driver = _driver(router)
    result = _capability(driver).search("kinase", 2, _lease())
    assert [c.native_id for c in result.candidates] == ["1ABC", "2XYZ"]
    assert _search_body(router.of("search.rcsb.org")[0])["request_options"]["paginate"]["rows"] == 2


def test_search_accepts_the_compact_string_result_shape() -> None:
    router = _Router(
        search=lambda r: httpx.Response(
            200, json={"total_count": 1, "result_set": ["1ABC"]}
        ),
        graphql=lambda r: _gql(_gql_entry()),
    )
    driver = _driver(router)
    result = _capability(driver).search("kinase", 5, _lease())
    assert [c.native_id for c in result.candidates] == ["1ABC"]


def test_search_drops_computed_structure_models_into_an_empty_result() -> None:
    # A legitimate provider result class Phase 15 deliberately scopes out: a
    # computed model is EXCLUDED and can never receive authority=pdb.
    router = _Router(
        search=lambda r: _search("AF_AFB0M2T2F1", "MA_3J3Q"),
        graphql=lambda r: _gql(),
    )
    driver = _driver(router)
    result = _capability(driver).search("hemoglobin", 5, _lease())
    assert result.candidates == ()
    # No metadata request is made when every hit was excluded.
    assert router.of("data.rcsb.org") == []


def test_search_maps_the_canonical_provider_id_for_an_extended_hit() -> None:
    router = _Router(
        search=lambda r: _search("1ABC"),
        graphql=lambda r: _gql(_gql_entry("1ABC")),
    )
    driver = _driver(router)
    result = _capability(driver).search("kinase", 5, _lease())
    assert result.candidates[0].native_id == "1ABC"


def test_search_empty_result_is_not_an_error() -> None:
    # RCSB answers a no-hit search with HTTP 204 and an EMPTY body.
    router = _Router(search=lambda r: httpx.Response(204), graphql=lambda r: _gql())
    driver = _driver(router)
    result = _capability(driver).search("nothing matches this", 5, _lease())
    assert result.candidates == ()


def test_search_without_a_result_set_is_an_empty_result() -> None:
    router = _Router(
        search=lambda r: httpx.Response(200, json={"total_count": 0}),
        graphql=lambda r: _gql(),
    )
    driver = _driver(router)
    assert _capability(driver).search("nothing", 5, _lease()).candidates == ()


def test_search_requires_exactly_the_selected_provider_and_bounded_input() -> None:
    router = _Router(search=lambda r: _search("1ABC"), graphql=lambda r: _gql(_gql_entry()))
    capability = _capability(_driver(router))
    with pytest.raises(CapabilityError) as empty:
        capability.search("   ", 5, _lease())
    assert empty.value.kind is CapabilityErrorKind.INVALID_PARAM
    with pytest.raises(CapabilityError) as long:
        capability.search("x" * (MAX_STRUCTURE_QUERY_CHARS + 1), 5, _lease())
    assert long.value.kind is CapabilityErrorKind.INVALID_PARAM
    with pytest.raises(CapabilityError) as limit:
        capability.search("kinase", 0, _lease())
    assert limit.value.kind is CapabilityErrorKind.INVALID_PARAM
    # An over-large limit is clamped, never rejected and never widened.
    assert _capability(_driver(_Router(search=lambda r: _search(), graphql=lambda r: _gql()))).search(
        "kinase", MAX_STRUCTURE_RESULT_LIMIT + 100, _lease()
    ).candidates == ()
    # Nothing reached the network for any of the rejected inputs above.
    assert router.requests == []


def test_search_fails_closed_on_a_malformed_search_envelope() -> None:
    for payload in ({"result_set": "nope"}, {"result_set": [1]}, {"result_set": [{"score": 1}]}):
        router = _Router(
            search=lambda r, payload=payload: httpx.Response(200, json=payload),
            graphql=lambda r: _gql(),
        )
        with pytest.raises(CapabilityError) as exc:
            _capability(_driver(router)).search("kinase", 5, _lease())
        assert exc.value.kind is CapabilityErrorKind.UNKNOWN


def test_search_fails_closed_on_a_malformed_metadata_envelope() -> None:
    envelopes = (
        httpx.Response(200, json={"errors": [{"message": "bad"}]}),
        httpx.Response(200, json={"data": "nope"}),
        httpx.Response(200, json={"data": {"entries": "nope"}}),
        httpx.Response(200, json={"data": {"entries": ["nope"]}}),
        httpx.Response(200, content=b"<html>not json</html>"),
    )
    for envelope in envelopes:
        router = _Router(
            search=lambda r: _search("1ABC"),
            graphql=lambda r, envelope=envelope: envelope,
        )
        with pytest.raises(CapabilityError) as exc:
            _capability(_driver(router)).search("kinase", 5, _lease())
        assert exc.value.kind is CapabilityErrorKind.UNKNOWN


def test_search_fails_closed_when_a_hit_has_no_metadata() -> None:
    router = _Router(search=lambda r: _search("1ABC"), graphql=lambda r: _gql())
    with pytest.raises(CapabilityError) as exc:
        _capability(_driver(router)).search("kinase", 5, _lease())
    assert exc.value.kind is CapabilityErrorKind.UNKNOWN


# ---------------------------------------------------------------------------
# resolution: CURRENT re-resolution + bounded canonical coordinate download
# ---------------------------------------------------------------------------


def _resolve_router(
    entry: dict | None = None, *, files=None
) -> _Router:
    return _Router(
        search=lambda r: _search(),
        graphql=lambda r: _gql(entry if entry is not None else _gql_entry()),
        files=files or (lambda r: _coordinate_response()),
    )


def test_resolve_returns_the_exact_canonical_snapshot() -> None:
    router = _resolve_router()
    record = _capability(_driver(router)).resolve(RCSB_AUTHORITY, "1abc", _lease())

    assert record.provider_key == RCSB_PROVIDER_KEY
    assert record.authority == RCSB_AUTHORITY
    assert record.native_id == "1ABC"
    assert record.coordinate_format == STRUCTURE_COORDINATE_FORMAT
    assert record.coordinate_bytes == COORDINATE_TEXT.encode()
    assert record.title == "Synthetic structure"
    assert record.experimental_methods == ("X-RAY DIFFRACTION",)
    assert record.resolution_angstrom == 1.5
    assert (record.entry_revision_major, record.entry_revision_minor) == (3, 1)
    assert record.entry_revision_date == "2026-08-12T00:00:00Z"
    # Coordinate bytes are hidden from repr/logging.
    assert COORDINATE_TEXT not in repr(record)
    # The file host is the FIXED documented host, and only one download happened.
    file_requests = router.of("files.rcsb.org")
    assert len(file_requests) == 1
    assert file_requests[0].url.path == COORDINATE_PATH


def test_resolve_normalizes_the_documented_extended_alias() -> None:
    # The Search/Data APIs accept only the classic id today, while the file host
    # accepts both. An extended request is looked up through its documented alias
    # and the provider-confirmed canonical id is stored.
    router = _resolve_router()
    record = _capability(_driver(router)).resolve(RCSB_AUTHORITY, "pdb_00001abc", _lease())
    assert record.native_id == "1ABC"
    assert _gql_variables(router.of("data.rcsb.org")[0]) == {"entry_ids": ["1ABC"]}


def test_resolve_looks_up_an_extended_only_identifier_as_given() -> None:
    # A future extended-only id has no legacy alias, so it is sent as-is (and the
    # provider's answer is what decides). This proves no four-character assumption.
    router = _Router(
        search=lambda r: _search(),
        graphql=lambda r: _gql(),
        files=lambda r: _coordinate_response(),
    )
    with pytest.raises(CapabilityError) as exc:
        _capability(_driver(router)).resolve(RCSB_AUTHORITY, "pdb_1abc5678", _lease())
    assert exc.value.kind is CapabilityErrorKind.NOT_FOUND
    assert _gql_variables(router.of("data.rcsb.org")[0]) == {"entry_ids": ["pdb_1abc5678"]}


@pytest.mark.parametrize("value", ["", "1AB", "1ABCD", "abc", "pdb_0001abc", "1a b"])
def test_resolve_rejects_a_malformed_identifier_before_any_request(value: str) -> None:
    router = _resolve_router()
    with pytest.raises(CapabilityError) as exc:
        _capability(_driver(router)).resolve(RCSB_AUTHORITY, value, _lease())
    assert exc.value.kind is CapabilityErrorKind.INVALID_PARAM
    assert router.requests == []


def test_resolve_rejects_a_computed_structure_model_with_a_named_deferral() -> None:
    router = _resolve_router()
    with pytest.raises(CapabilityError) as exc:
        _capability(_driver(router)).resolve(RCSB_AUTHORITY, "AF_AFB0M2T2F1", _lease())
    assert exc.value.kind is CapabilityErrorKind.INVALID_PARAM
    assert "Computed Structure Model" in exc.value.message
    assert router.requests == []


def test_resolve_rejects_a_non_experimental_methodology() -> None:
    for methodology in ("computational", "integrative", None):
        router = _resolve_router(_gql_entry(methodology=methodology))
        with pytest.raises(CapabilityError) as exc:
            _capability(_driver(router)).resolve(RCSB_AUTHORITY, "1ABC", _lease())
        assert exc.value.kind is CapabilityErrorKind.INVALID_PARAM
        # No coordinate bytes are ever downloaded for a rejected entry.
        assert router.of("files.rcsb.org") == []


def test_resolve_rejects_a_foreign_authority() -> None:
    router = _resolve_router()
    with pytest.raises(CapabilityError) as exc:
        _capability(_driver(router)).resolve("uniprot", "P12345", _lease())
    assert exc.value.kind is CapabilityErrorKind.INVALID_PARAM
    assert router.requests == []


def test_resolve_fails_closed_on_an_unknown_entry() -> None:
    router = _resolve_router(None)
    router._handlers[("data.rcsb.org", GRAPHQL_PATH)] = lambda r: _gql()
    with pytest.raises(CapabilityError) as exc:
        _capability(_driver(router)).resolve(RCSB_AUTHORITY, "1ABC", _lease())
    assert exc.value.kind is CapabilityErrorKind.NOT_FOUND


def test_resolve_fails_closed_when_the_provider_substitutes_an_identity() -> None:
    router = _resolve_router(_gql_entry("2XYZ"))
    with pytest.raises(CapabilityError) as exc:
        _capability(_driver(router)).resolve(RCSB_AUTHORITY, "1ABC", _lease())
    assert exc.value.kind is CapabilityErrorKind.UNKNOWN
    assert router.of("files.rcsb.org") == []


# ---------------------------------------------------------------------------
# metadata normalization: methods and resolution
# ---------------------------------------------------------------------------


def test_multiple_methods_are_deduplicated_sorted_and_bounded() -> None:
    entry = _gql_entry(methods=("X-RAY DIFFRACTION", "NEUTRON DIFFRACTION", "x-ray diffraction"))
    router = _resolve_router(entry)
    record = _capability(_driver(router)).resolve(RCSB_AUTHORITY, "1ABC", _lease())
    assert record.experimental_methods == ("NEUTRON DIFFRACTION", "X-RAY DIFFRACTION")


def test_an_absurd_method_list_fails_closed() -> None:
    entry = _gql_entry(methods=tuple(f"METHOD {i}" for i in range(MAX_STRUCTURE_METHODS + 1)))
    router = _resolve_router(entry)
    with pytest.raises(CapabilityError) as exc:
        _capability(_driver(router)).resolve(RCSB_AUTHORITY, "1ABC", _lease())
    assert exc.value.kind is CapabilityErrorKind.UNKNOWN


def test_missing_methods_is_allowed() -> None:
    router = _resolve_router(_gql_entry(methods=None, extra={"exptl": []}))
    record = _capability(_driver(router)).resolve(RCSB_AUTHORITY, "1ABC", _lease())
    assert record.experimental_methods == ()


def test_resolution_is_the_best_minimum_of_the_provider_array() -> None:
    # `resolution_combined` is an UNSORTED array with one value per method.
    router = _resolve_router(_gql_entry(resolution=(2.4, 1.65)))
    record = _capability(_driver(router)).resolve(RCSB_AUTHORITY, "1ABC", _lease())
    assert record.resolution_angstrom == 1.65


@pytest.mark.parametrize(
    "resolution", [None, [], [None], ["x"], [0], [-1.0], [True], "1.5", 1.5]
)
def test_a_non_applicable_or_malformed_resolution_stays_null(resolution: object) -> None:
    router = _resolve_router(_gql_entry(resolution=resolution))
    record = _capability(_driver(router)).resolve(RCSB_AUTHORITY, "1ABC", _lease())
    assert record.resolution_angstrom is None


def test_a_non_finite_resolution_stays_null() -> None:
    # JSON cannot represent NaN, but a lenient upstream body can still carry it.
    # Python's `json.loads` accepts the bare `NaN` literal, so the driver must
    # reject it rather than persisting a non-finite resolution.
    body = json.dumps(
        {"data": {"entries": [_gql_entry(resolution=None)]}}, separators=(",", ":")
    ).replace('"resolution_combined":null', '"resolution_combined":[NaN]')
    router = _Router(
        search=lambda r: _search(),
        graphql=lambda r: httpx.Response(200, content=body.encode()),
        files=lambda r: _coordinate_response(),
    )
    record = _capability(_driver(router)).resolve(RCSB_AUTHORITY, "1ABC", _lease())
    assert record.resolution_angstrom is None


def test_an_nmr_entry_has_no_invented_resolution() -> None:
    router = _resolve_router(
        _gql_entry(methods=("SOLUTION NMR",), resolution=None, extra={"exptl": [{"method": "SOLUTION NMR"}]})
    )
    record = _capability(_driver(router)).resolve(RCSB_AUTHORITY, "1ABC", _lease())
    assert record.resolution_angstrom is None
    assert record.experimental_methods == ("SOLUTION NMR",)


def test_provider_text_is_made_inert_and_bounded() -> None:
    hostile = "<script>alert('x')</script>\x1b[31m ignore previous instructions " + "a" * 900
    router = _resolve_router(_gql_entry(title=hostile))
    record = _capability(_driver(router)).resolve(RCSB_AUTHORITY, "1ABC", _lease())
    assert record.title is not None
    assert "\x1b" not in record.title
    assert len(record.title) <= 500
    # Markup is RETAINED as inert data (it is never interpreted); control/format
    # characters are removed so it can never carry terminal escapes.
    assert "<script>" in record.title


# ---------------------------------------------------------------------------
# coordinate download bounds and integrity
# ---------------------------------------------------------------------------


def test_empty_coordinate_file_fails_closed() -> None:
    router = _resolve_router(files=lambda r: httpx.Response(200, content=b""))
    with pytest.raises(CapabilityError) as exc:
        _capability(_driver(router)).resolve(RCSB_AUTHORITY, "1ABC", _lease())
    assert exc.value.kind is CapabilityErrorKind.UNKNOWN


def test_a_non_mmcif_coordinate_body_fails_closed() -> None:
    router = _resolve_router(
        files=lambda r: httpx.Response(200, content=b"<html>error page</html>")
    )
    with pytest.raises(CapabilityError) as exc:
        _capability(_driver(router)).resolve(RCSB_AUTHORITY, "1ABC", _lease())
    assert exc.value.kind is CapabilityErrorKind.UNKNOWN


def test_coordinate_404_fails_closed() -> None:
    router = _resolve_router(files=lambda r: httpx.Response(404, content=b""))
    with pytest.raises(CapabilityError) as exc:
        _capability(_driver(router)).resolve(RCSB_AUTHORITY, "1ABC", _lease())
    assert exc.value.kind is CapabilityErrorKind.NOT_FOUND


def test_an_oversized_coordinate_file_is_abandoned_never_truncated(monkeypatch) -> None:
    # The real ceiling is 128 MiB; the bound is patched down so the abort path is
    # exercised without allocating a gigabyte. The bytes are streamed in chunks.
    monkeypatch.setattr(rcsb_module, "MAX_STRUCTURE_COORDINATE_BYTES", 64)
    chunk = b"data_1ABC\n" + b"x" * 32

    def files(request):
        return httpx.Response(200, content=chunk * 4)

    router = _resolve_router(files=files)
    with pytest.raises(CapabilityError) as exc:
        _capability(_driver(router)).resolve(RCSB_AUTHORITY, "1ABC", _lease())
    assert exc.value.kind is CapabilityErrorKind.PROVIDER_UNAVAILABLE


def test_the_coordinate_ceiling_is_the_documented_value() -> None:
    # A defensible scientific/operational ceiling (128 MiB), well above the largest
    # deposited archive entry while bounding one import.
    assert rcsb_module.MAX_STRUCTURE_COORDINATE_BYTES == 134_217_728


def test_a_truncated_coordinate_transport_fails_closed() -> None:
    def files(request):
        raise httpx.ReadError("connection dropped", request=request)

    router = _resolve_router(files=files)
    with pytest.raises(CapabilityError) as exc:
        _capability(_driver(router)).resolve(RCSB_AUTHORITY, "1ABC", _lease())
    assert exc.value.kind is CapabilityErrorKind.NETWORK


# ---------------------------------------------------------------------------
# transport / status / rate-policy translation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "kind", "retryable"),
    [
        (400, CapabilityErrorKind.INVALID_PARAM, False),
        (401, CapabilityErrorKind.AUTH, False),
        (403, CapabilityErrorKind.AUTH, False),
        (404, CapabilityErrorKind.NOT_FOUND, False),
        (410, CapabilityErrorKind.NOT_FOUND, False),
        (422, CapabilityErrorKind.INVALID_PARAM, False),
        (429, CapabilityErrorKind.PROVIDER_UNAVAILABLE, True),
        (500, CapabilityErrorKind.PROVIDER_UNAVAILABLE, True),
        (503, CapabilityErrorKind.PROVIDER_UNAVAILABLE, True),
        (418, CapabilityErrorKind.UNKNOWN, False),
    ],
)
def test_provider_statuses_translate_to_typed_failures(
    status: int, kind: CapabilityErrorKind, retryable: bool
) -> None:
    router = _Router(
        search=lambda r: httpx.Response(status, content=b"upstream body"),
        graphql=lambda r: _gql(),
    )
    with pytest.raises(CapabilityError) as exc:
        _capability(_driver(router)).search("kinase", 5, _lease())
    assert exc.value.kind is kind
    assert exc.value.retryable is retryable
    assert exc.value.upstream_status == status


def test_a_redirect_is_not_followed_and_fails_closed() -> None:
    router = _Router(
        search=lambda r: httpx.Response(302, headers={"location": "https://evil.test/x"}),
        graphql=lambda r: _gql(),
    )
    driver = _driver(router)
    with pytest.raises(CapabilityError) as exc:
        _capability(driver).search("kinase", 5, _lease())
    assert exc.value.kind is CapabilityErrorKind.UNKNOWN
    assert exc.value.upstream_status == 302
    # Exactly one request: the redirect target was never contacted.
    assert len(router.requests) == 1
    assert driver._search_client is not None
    assert driver._search_client.follow_redirects is False


def test_timeouts_and_connect_errors_are_typed_network_failures() -> None:
    def timeout(request):
        raise httpx.ReadTimeout("timed out", request=request)

    def connect(request):
        raise httpx.ConnectError("no route", request=request)

    for handler in (timeout, connect):
        router = _Router(search=handler, graphql=lambda r: _gql())
        with pytest.raises(CapabilityError) as exc:
            _capability(_driver(router)).search("kinase", 5, _lease())
        assert exc.value.kind is CapabilityErrorKind.NETWORK
        assert exc.value.retryable is True


def test_an_oversized_metadata_response_fails_closed() -> None:
    router = _Router(
        search=lambda r: httpx.Response(
            200, content=b'{"result_set":[' + b" " * (MAX_API_RESPONSE_BYTES + 16) + b"]}"
        ),
        graphql=lambda r: _gql(),
    )
    with pytest.raises(CapabilityError) as exc:
        _capability(_driver(router)).search("kinase", 5, _lease())
    assert exc.value.kind is CapabilityErrorKind.PROVIDER_UNAVAILABLE


def test_errors_never_leak_the_url_query_identifier_or_upstream_body() -> None:
    sentinel = "REVOLAB_SENTINEL_0f9e2a7c4b6d8e1f"
    router = _Router(
        search=lambda r: httpx.Response(500, content=sentinel.encode()),
        graphql=lambda r: _gql(),
    )
    with pytest.raises(CapabilityError) as exc:
        _capability(_driver(router)).search(f"secret {sentinel}", 5, _lease())
    text = f"{exc.value} {exc.value!r}"
    assert sentinel not in text
    assert "search.rcsb.org" not in text
    assert "https://" not in text


def test_the_clients_use_only_the_three_fixed_hosts_without_ambient_proxy_config() -> None:
    driver = _driver(_Router(search=lambda r: _search(), graphql=lambda r: _gql()))
    clients = [driver._search_client, driver._data_client, driver._files_client]
    for client in clients:
        assert client is not None
        assert client.follow_redirects is False
        assert client.trust_env is False
    assert driver._search_client is not None and str(driver._search_client.base_url) == RCSB_SEARCH_BASE_URL
    assert driver._data_client is not None and str(driver._data_client.base_url) == RCSB_DATA_BASE_URL
    assert driver._files_client is not None and str(driver._files_client.base_url) == RCSB_FILES_BASE_URL


def test_stop_closes_the_clients_and_clears_capabilities() -> None:
    driver = _driver(_Router(search=lambda r: _search(), graphql=lambda r: _gql()))
    driver.stop()
    assert driver.capabilities == {}
    assert driver._search_client is None
    assert driver._data_client is None
    assert driver._files_client is None


# ---------------------------------------------------------------------------
# health probe
# ---------------------------------------------------------------------------


def test_health_probe_is_bounded_and_uses_the_fixed_search_host() -> None:
    router = _Router(search=lambda r: _search("1ABC"), graphql=lambda r: _gql())
    driver = _driver(router)
    assert driver.probe_health() is ProviderRuntimeHealth.READY
    assert len(router.requests) == 1
    body = _search_body(router.requests[0])
    assert body["request_options"]["paginate"]["rows"] == 1


def test_health_probe_reports_degraded_and_unreachable() -> None:
    degraded = _driver(_Router(search=lambda r: httpx.Response(503), graphql=lambda r: _gql()))
    assert degraded.probe_health() is ProviderRuntimeHealth.DEGRADED

    def boom(request):
        raise httpx.ConnectError("no route", request=request)

    unreachable = _driver(_Router(search=boom, graphql=lambda r: _gql()))
    assert unreachable.probe_health() is ProviderRuntimeHealth.UNREACHABLE


def test_health_probe_before_start_is_unreachable() -> None:
    assert RcsbDriver().probe_health() is ProviderRuntimeHealth.UNREACHABLE


def test_health_probe_treats_an_empty_result_as_healthy() -> None:
    driver = _driver(_Router(search=lambda r: httpx.Response(204), graphql=lambda r: _gql()))
    assert driver.probe_health() is ProviderRuntimeHealth.READY


# ---------------------------------------------------------------------------
# Review round 1 fixes
# ---------------------------------------------------------------------------


def test_search_fails_closed_on_a_non_experimental_entry() -> None:
    """`results_content_type` does NOT exclude integrative entries (live-verified).

    The experimental-archive constraint is therefore re-validated on the returned
    METADATA, so an experimental-only query can never yield a candidate carrying
    `authority="pdb"` for a computational or integrative entry.
    """
    for methodology in ("integrative", "computational", None):
        router = _Router(
            search=lambda r: _search("1ABC"),
            graphql=lambda r, methodology=methodology: _gql(
                _gql_entry(methodology=methodology)
            ),
        )
        with pytest.raises(CapabilityError) as exc:
            _capability(_driver(router)).search("kinase", 5, _lease())
        assert exc.value.kind is CapabilityErrorKind.UNKNOWN


def test_search_fails_closed_on_a_present_but_unparseable_method_list() -> None:
    """An entry that reports experiments but yields no method is NOT "no method"."""
    malformed = _gql_entry(methods=None, extra={"exptl": ["X-RAY DIFFRACTION"]})
    router = _Router(search=lambda r: _search("1ABC"), graphql=lambda r: _gql(malformed))
    with pytest.raises(CapabilityError) as exc:
        _capability(_driver(router)).search("kinase", 5, _lease())
    assert exc.value.kind is CapabilityErrorKind.UNKNOWN


def test_search_treats_a_genuinely_empty_method_list_as_absent() -> None:
    empty = _gql_entry(methods=None, extra={"exptl": []})
    router = _Router(search=lambda r: _search("1ABC"), graphql=lambda r: _gql(empty))
    result = _capability(_driver(router)).search("kinase", 5, _lease())
    assert result.candidates[0].experimental_methods == ()


def test_search_indexes_metadata_under_both_identifier_forms() -> None:
    """A future extended-primary-id response must not become "no metadata".

    The hit uses the classic id while the Data API confirms the documented extended
    alias; the index is keyed by BOTH forms, so the lookup still resolves.
    """
    router = _Router(
        search=lambda r: _search("1ABC"),
        graphql=lambda r: _gql(_gql_entry("pdb_00001abc")),
    )
    result = _capability(_driver(router)).search("kinase", 5, _lease())
    assert [candidate.native_id for candidate in result.candidates] == ["pdb_00001abc"]
