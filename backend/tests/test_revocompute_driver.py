"""REvoCompute driver contract tests over an HTTP fake at the network boundary.

The fake stands in for REvoCompute's HTTP surface; the driver translation itself
is exercised for real (no mocking of the driver). The handler asserts the
synthetic sentinel appears ONLY inside the permitted X-API-Key header, never in
URLs, query strings, other headers, or logged/error surfaces.
"""

from __future__ import annotations

import hashlib
from urllib.parse import unquote

import httpx
import pytest

from revolab.capabilities import CapabilityError, ExternalArtifactRef, ResolvedInput
from revolab.credentials import CredentialLease
from revolab.drivers import DriverContext
from revolab.drivers.revocompute import REvoComputeDriver
from revolab.enums import CapabilityErrorKind, CapabilityKind, ProviderRuntimeHealth

SENTINEL = "REVOLAB_COMPUTE_SENTINEL_1a2b3c4d5e6f7a8b"

TASK_ID = "abcdef0123456789abcdef0123456789"


def _types_payload() -> dict:
    return {
        "version": 2,
        "categories": [{"name": "structure", "label": "Structure", "description": "", "order": 1}],
        "task_types": [
            {
                "name": "echo",
                "display_name": "Echo",
                "category": "structure",
                "summary": "Echo task",
                "use_when": "",
                "input_summary": "",
                "output_summary": "",
                "considerations": [],
                "gpus": False,
                "requires_network": False,
                "input_extensions": [".fasta"],
                "input_label": "FASTA",
                "stage_markers": {},
                "access": {"restricted": False},
                "params": [
                    {
                        "name": "model_type",
                        "type": "str",
                        "default": "auto",
                        "required": False,
                        "description": "",
                        "label": "Model Type",
                        "choices": ["auto", "plain"],
                        "minimum": None,
                        "maximum": None,
                        "step": None,
                        "unit": "",
                        "advanced": False,
                    },
                    {"name": "num_models", "type": "int", "default": 5, "required": True,
                     "description": "how many", "label": "Num Models", "choices": [],
                     "minimum": 1, "maximum": 5, "step": None, "unit": "", "advanced": False},
                ],
            }
        ],
    }


def _schema_payload() -> dict:
    return {
        "name": "echo",
        "display_name": "Echo",
        "category": "structure",
        "summary": "Echo task",
        "definition_version": 3,
        "runtime_family": "echo",
        "citations": [],
        "workflow": [],
        "file_input": {
            "accept": ".fasta",
            "extensions": [".fasta"],
            "primary_extensions": [".fasta"],
            "label": "FASTA",
            "required": True,
            "multiple": False,
            "max_files": 1,
            "max_request_bytes": 10485760,
        },
        "params": [
            {"name": "model_type", "type": "str", "default": "auto", "required": False,
             "description": "", "label": "Model Type", "choices": ["auto", "plain"], "help": ""},
            {"name": "num_models", "type": "int", "default": 5, "required": True,
             "description": "how many", "label": "Num Models", "choices": [], "help": ""},
        ],
    }


def _lease() -> CredentialLease:
    return CredentialLease({"api_key": SENTINEL})


def _driver(handler) -> REvoComputeDriver:
    transport = httpx.MockTransport(handler)
    driver = REvoComputeDriver(transport=transport)
    driver.start(
        DriverContext(
            environment="test",
            settings={"revocompute_base_url": "https://revocompute.test", "revocompute_timeout_seconds": 5.0},
        )
    )
    return driver


def _compute(driver: REvoComputeDriver):
    return driver.capabilities[CapabilityKind.COMPUTE]


def _assert_sentinel_only_in_api_key(request: httpx.Request) -> None:
    assert request.headers.get("X-API-Key") == SENTINEL
    assert SENTINEL not in str(request.url)
    assert SENTINEL not in str({k: v for k, v in request.headers.items() if k != "x-api-key"})


def test_health_probe() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/compute/health":
            return httpx.Response(200)
        return httpx.Response(404)

    driver = _driver(handler)
    assert driver.probe_health() is ProviderRuntimeHealth.READY
    assert any(request.url.path == "/compute/health" for request in seen)


def test_list_task_kinds_parses_catalog() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_sentinel_only_in_api_key(request)
        if request.url.path == "/compute/api/types":
            return httpx.Response(200, json=_types_payload())
        return httpx.Response(404)

    driver = _driver(handler)
    kinds = _compute(driver).list_task_kinds(_lease())
    assert [k.kind_id for k in kinds] == ["echo"]
    assert kinds[0].display_name == "Echo"


def test_task_kind_schema_translation_is_json_schema() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_sentinel_only_in_api_key(request)
        return httpx.Response(200, json=_schema_payload())

    driver = _driver(handler)
    schema = _compute(driver).task_kind_schema("echo", _lease())
    assert schema.parameter_schema["type"] == "object"
    assert schema.parameter_schema["properties"]["model_type"]["enum"] == ["auto", "plain"]
    assert schema.parameter_schema["properties"]["num_models"]["type"] == "integer"
    assert schema.parameter_schema["required"] == ["num_models"]
    assert schema.input_spec.required is True
    assert schema.input_spec.accepted_extensions == (".fasta",)


def test_submit_follows_redirect_and_extracts_task_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_sentinel_only_in_api_key(request)
        if request.url.path == "/compute/api/types/echo":
            return httpx.Response(200, json=_schema_payload())
        if request.url.path == "/compute/api/post":
            body = request.content.decode()
            assert "task_type" in body
            assert "params[model_type]" in body
            assert b">fasta\n" in request.content  # uploaded file bytes present in multipart
            return httpx.Response(
                302,
                headers={"Location": f"https://revocompute.test/compute/api/running/{TASK_ID}"},
            )
        if request.url.path == f"/compute/api/running/{TASK_ID}":
            return httpx.Response(200, json={"status": "pending", "md5sum": TASK_ID})
        return httpx.Response(404)

    driver = _driver(handler)
    handle = _compute(driver).submit(
        "echo",
        [ResolvedInput(role=None, filename="input.fasta", content_type="text/plain", data=b">fasta\nATCG")],
        {"model_type": "auto", "num_models": 2},
        _lease(),
    )
    assert handle.authority == "revocompute"
    assert handle.native_id == TASK_ID
    assert handle.task_type == "echo"


def test_submit_accepts_relative_same_origin_redirect() -> None:
    """REvoCompute emits exactly `redirect(f"/compute/api/running/{md5sum}")`:
    the Location is RELATIVE to the request origin."""

    def handler(request: httpx.Request) -> httpx.Response:
        _assert_sentinel_only_in_api_key(request)
        if request.url.path == "/compute/api/types/echo":
            return httpx.Response(200, json=_schema_payload())
        if request.url.path == "/compute/api/post":
            return httpx.Response(
                302, headers={"Location": f"/compute/api/running/{TASK_ID}"}
            )
        return httpx.Response(404)

    driver = _driver(handler)
    handle = _compute(driver).submit(
        "echo",
        [ResolvedInput(role=None, filename="input.fasta", content_type="text/plain", data=b">x\nAC")],
        {},
        _lease(),
    )
    assert handle.native_id == TASK_ID


def test_submit_references_prior_artifact_without_uploading_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_sentinel_only_in_api_key(request)
        if request.url.path == "/compute/api/types/echo":
            return httpx.Response(200, json=_schema_payload())
        if request.url.path == "/compute/api/post":
            body = unquote(request.content.decode())
            assert f"@{TASK_ID}/out.pdb" in body
            assert "artifact_references" in body
            return httpx.Response(
                302, headers={"Location": f"https://revocompute.test/compute/api/running/{TASK_ID}"}
            )
        if request.url.path == f"/compute/api/running/{TASK_ID}":
            return httpx.Response(200, json={"status": "finished", "md5sum": TASK_ID})
        return httpx.Response(404)

    driver = _driver(handler)
    handle = _compute(driver).submit(
        "echo",
        [ResolvedInput(role=None, filename="prior", external=ExternalArtifactRef(
            authority="revocompute", native_id=f"{TASK_ID}:out.pdb", version_id=""))],
        {},
        _lease(),
    )
    assert handle.native_id == TASK_ID


def test_get_run_distinguishes_failed_on_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_sentinel_only_in_api_key(request)
        return httpx.Response(404, json={"status": "failed", "md5sum": TASK_ID, "error": "boom"})

    driver = _driver(handler)
    view = _compute(driver).get_run(TASK_ID, _lease())
    assert view.status == "failed"
    assert view.status_detail == "boom"


def test_list_artifacts_encodes_identity_without_filesystem_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_sentinel_only_in_api_key(request)
        return httpx.Response(
            200,
            json={
                "status": "finished",
                "artifacts": [
                    {"path": "out.pdb", "size": 101, "sha256": "aa" * 32, "media_type": "chemical/x-pdb",
                     "role": "primary"}
                ],
                "result": {"files": {}},
            },
        )

    driver = _driver(handler)
    artifacts = _compute(driver).list_artifacts(TASK_ID, _lease())
    assert len(artifacts) == 1
    assert artifacts[0].native_id == f"{TASK_ID}:out.pdb"
    assert artifacts[0].checksum == "aa" * 32
    assert artifacts[0].size == 101


def test_resolve_artifact_returns_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_sentinel_only_in_api_key(request)
        return httpx.Response(200, content=b"ARTIFACT-BYTES")

    driver = _driver(handler)
    cap = driver.capabilities[CapabilityKind.ARTIFACT_RESOLUTION]
    resolved = cap.resolve(
        ExternalArtifactRef(authority="revocompute", native_id=f"{TASK_ID}:out.pdb", version_id=""),
        _lease(),
    )
    assert resolved.data == b"ARTIFACT-BYTES"
    assert resolved.native_id == f"{TASK_ID}:out.pdb"


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, CapabilityErrorKind.AUTH), (403, CapabilityErrorKind.AUTH),
     (404, CapabilityErrorKind.NOT_FOUND), (400, CapabilityErrorKind.INVALID_PARAM),
     (503, CapabilityErrorKind.PROVIDER_UNAVAILABLE)],
)
def test_typed_error_mapping(status: int, kind: CapabilityErrorKind) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "upstream says no"})

    driver = _driver(handler)
    with pytest.raises(CapabilityError) as excinfo:
        _compute(driver).get_run(TASK_ID, _lease())
    assert excinfo.value.kind is kind
    assert SENTINEL not in str(excinfo.value)
    assert SENTINEL not in repr(excinfo.value)


def test_network_failure_maps_to_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    driver = _driver(handler)
    with pytest.raises(CapabilityError) as excinfo:
        _compute(driver).get_run(TASK_ID, _lease())
    assert excinfo.value.kind is CapabilityErrorKind.NETWORK
    assert SENTINEL not in str(excinfo.value)


def test_malformed_upstream_response_maps_to_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>", headers={"content-type": "text/html"})

    driver = _driver(handler)
    with pytest.raises(CapabilityError) as excinfo:
        _compute(driver).list_task_kinds(_lease())
    assert excinfo.value.kind is CapabilityErrorKind.UNKNOWN


@pytest.mark.parametrize(
    ("status", "kind"),
    [(400, CapabilityErrorKind.INVALID_PARAM), (422, CapabilityErrorKind.INVALID_PARAM),
     (401, CapabilityErrorKind.AUTH), (403, CapabilityErrorKind.AUTH),
     (429, CapabilityErrorKind.PROVIDER_UNAVAILABLE), (503, CapabilityErrorKind.PROVIDER_UNAVAILABLE)],
)
def test_submit_maps_upstream_status_to_typed_kind(status: int, kind: CapabilityErrorKind) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_sentinel_only_in_api_key(request)
        if request.url.path == "/compute/api/types/echo":
            return httpx.Response(200, json=_schema_payload())
        if request.url.path == "/compute/api/post":
            return httpx.Response(status, json={"error": "upstream says no"})
        return httpx.Response(404)

    driver = _driver(handler)
    with pytest.raises(CapabilityError) as excinfo:
        _compute(driver).submit(
            "echo",
            [ResolvedInput(role=None, filename="input.fasta", content_type="text/plain", data=b">x\nAC")],
            {},
            _lease(),
        )
    assert excinfo.value.kind is kind
    assert SENTINEL not in str(excinfo.value)


def test_submit_rejects_input_extension_not_accepted_by_task() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_sentinel_only_in_api_key(request)
        if request.url.path == "/compute/api/types/echo":
            return httpx.Response(200, json=_schema_payload())  # accepts only .fasta
        return httpx.Response(404)

    driver = _driver(handler)
    with pytest.raises(CapabilityError) as excinfo:
        _compute(driver).submit(
            "echo",
            [ResolvedInput(role=None, filename="revision-abc.json", content_type="application/json", data=b"{}")],
            {},
            _lease(),
        )
    assert excinfo.value.kind is CapabilityErrorKind.INVALID_PARAM


def test_submit_does_not_forward_credential_on_cross_origin_redirect() -> None:
    seen_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        _assert_sentinel_only_in_api_key(request)
        seen_hosts.append(str(request.url.host))
        if request.url.path == "/compute/api/types/echo":
            return httpx.Response(200, json=_schema_payload())
        if request.url.path == "/compute/api/post":
            return httpx.Response(
                302, headers={"Location": f"https://evil.example/compute/api/running/{TASK_ID}"}
            )
        return httpx.Response(404)

    driver = _driver(handler)
    with pytest.raises(CapabilityError) as excinfo:
        _compute(driver).submit(
            "echo",
            [ResolvedInput(role=None, filename="input.fasta", content_type="text/plain", data=b">x\nAC")],
            {},
            _lease(),
        )
    assert excinfo.value.kind is CapabilityErrorKind.UNKNOWN  # cross-origin location is untrusted
    assert set(seen_hosts) == {"revocompute.test"}


def test_resolve_rejects_path_traversal() -> None:
    driver = _driver(lambda request: httpx.Response(404))
    cap = driver.capabilities[CapabilityKind.ARTIFACT_RESOLUTION]
    with pytest.raises(CapabilityError) as excinfo:
        cap.resolve(
            ExternalArtifactRef(authority="revocompute", native_id=f"{TASK_ID}:../secret", version_id=""),
            _lease(),
        )
    assert excinfo.value.kind is CapabilityErrorKind.INVALID_PARAM


def test_resolve_verifies_checksum_and_size() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _assert_sentinel_only_in_api_key(request)
        return httpx.Response(200, content=b"WRONG")

    driver = _driver(handler)
    cap = driver.capabilities[CapabilityKind.ARTIFACT_RESOLUTION]

    right = hashlib.sha256(b"RIGHT").hexdigest()
    with pytest.raises(CapabilityError) as excinfo:
        cap.resolve(
            ExternalArtifactRef(
                authority="revocompute", native_id=f"{TASK_ID}:out.pdb", version_id="",
                checksum=right, size=len(b"RIGHT"),
            ),
            _lease(),
        )
    assert excinfo.value.kind is CapabilityErrorKind.UNKNOWN
