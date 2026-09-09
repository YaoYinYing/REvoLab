"""REvoCompute provider driver (Phase 4).

The only module allowed to know REvoCompute's HTTP vocabulary: its routes, form
fields, task-type descriptor shape, task-id model, run-status strings and
artifact identity encoding. Everything above this module speaks the neutral
`revolab.capabilities` value objects. Core never imports this module.

This driver is config-driven and installed only when a REvoCompute base URL is
configured (see `revolab.bootstrap`); it is never imported for its side effects.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from revolab.capabilities import (
    ArtifactHandle,
    CapabilityError,
    ExternalArtifactRef,
    InputSpec,
    ResolvedInput,
    RunHandle,
    RunView,
    TaskKindRef,
    TaskKindSchema,
)
from revolab.credentials import CredentialLease
from revolab.drivers import DriverContext
from revolab.enums import CapabilityErrorKind, CapabilityKind, ProviderRuntimeHealth

REVOCOMPUTE_AUTHORITY = "revocompute"
REVOCOMPUTE_CREDENTIAL_KIND = "api_key"

_PROVIDER_KEY = "revocompute"

# Task ids are 32 lowercase hex characters (md5 digest, usedforsecurity=False).
_TASK_ID = frozenset("0123456789abcdefABCDEF")
_MIN_ID = 32


def _sanitize_task_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if len(text) < _MIN_ID or any(c not in _TASK_ID for c in text[:32]):
        return None
    return text[:32]


_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def _same_origin(base_url: str, target: str) -> bool:
    """A redirect target is followable only from the configured origin. The
    driver never follows off-origin redirects, so the X-API-Key credential is
    never replayed to another host."""
    try:
        base = urlsplit(base_url)
        location = urlsplit(target)
    except ValueError:
        return False
    return base.scheme == location.scheme and base.netloc == location.netloc


class REvoComputeComputeCapability:
    """`CapabilityKind.COMPUTE` realization over REvoCompute's HTTP API."""

    provider_key = _PROVIDER_KEY
    kind = CapabilityKind.COMPUTE

    def __init__(self, client: httpx.Client, base_url: str) -> None:
        self._client = client
        self._base_url = base_url

    # -- discovery -----------------------------------------------------------------

    def list_task_kinds(self, credentials: CredentialLease) -> list[TaskKindRef]:
        payload = self._request_json("GET", "/compute/api/types", credentials)
        task_types = payload.get("task_types", [])
        if not isinstance(task_types, list):
            raise self._error(
                CapabilityErrorKind.UNKNOWN, "unexpected task-type payload shape", None
            )
        return [
            TaskKindRef(
                kind_id=str(item.get("name", "")),
                display_name=str(item.get("display_name", item.get("name", ""))),
                description=item.get("summary"),
                category=item.get("category"),
            )
            for item in task_types
            if isinstance(item, dict) and item.get("name")
        ]

    def task_kind_schema(self, kind_id: str, credentials: CredentialLease) -> TaskKindSchema:
        payload = self._request_json(
            "GET", f"/compute/api/types/{quote(kind_id, safe='')}", credentials
        )
        return self._translate_task_kind_schema(kind_id, payload)

    def _translate_task_kind_schema(self, kind_id: str, payload: Mapping[str, Any]) -> TaskKindSchema:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for raw in payload.get("params", []) or []:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name", ""))
            if not name:
                continue
            descriptor = self._parameter_schema(raw)
            properties[name] = descriptor
            if raw.get("required"):
                required.append(name)
        file_input = payload.get("file_input") or {}
        input_spec = InputSpec(
            label=file_input.get("label"),
            required=bool(file_input.get("required")),
            multiple=bool(file_input.get("multiple")),
            max_files=file_input.get("max_files"),
            accepted_extensions=tuple(str(e) for e in (file_input.get("extensions") or [])),
        )
        parameter_schema: dict[str, Any] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }
        return TaskKindSchema(
            kind_id=kind_id,
            display_name=str(payload.get("display_name", kind_id)),
            description=payload.get("summary") or payload.get("input_summary"),
            parameter_schema=parameter_schema,
            input_spec=input_spec,
        )

    @staticmethod
    def _parameter_schema(raw: Mapping[str, Any]) -> dict[str, Any]:
        """Translate one flat REvoCompute parameter descriptor into a Draft
        2020-12 JSON Schema subschema (schema-as-data translation lives here)."""
        descriptor: dict[str, Any] = {}
        param_type = str(raw.get("type", "string"))
        if param_type in {"str", "string"}:
            descriptor["type"] = "string"
        elif param_type in {"int", "integer"}:
            descriptor["type"] = "integer"
        elif param_type == "float" or param_type == "number":
            descriptor["type"] = "number"
        elif param_type == "bool" or param_type == "boolean":
            descriptor["type"] = "boolean"
        else:
            descriptor["type"] = "string"
        if raw.get("description"):
            descriptor["description"] = str(raw["description"])
        title = raw.get("label") or raw.get("name")
        if title:
            descriptor["title"] = str(title)
        choices = raw.get("choices") or []
        if choices:
            descriptor["enum"] = list(choices)
        if raw.get("default") is not None:
            descriptor["default"] = raw.get("default")
        if raw.get("minimum") is not None:
            descriptor["minimum"] = raw.get("minimum")
        if raw.get("maximum") is not None:
            descriptor["maximum"] = raw.get("maximum")
        return descriptor

    # -- submission -----------------------------------------------------------------

    def submit(
        self,
        kind_id: str,
        inputs: Sequence[ResolvedInput],
        params: Mapping[str, Any],
        credentials: CredentialLease,
    ) -> RunHandle:
        schema = self.task_kind_schema(kind_id, credentials)
        accepted = {ext.lower() for ext in schema.input_spec.accepted_extensions}
        data: dict[str, Any] = {"task_type": kind_id}
        for key, value in params.items():
            # REvoCompute reads flat `params[key]=value` form fields. Scalars only.
            data[f"params[{key}]"] = self._form_value(value)
        files: list[tuple[str, tuple[str, bytes, str]]] = []
        artifact_refs: list[str] = []
        for item in inputs:
            if item.data is not None:
                extension = os.path.splitext(item.filename)[1].lower()
                if accepted and extension not in accepted:
                    raise self._error(
                        CapabilityErrorKind.INVALID_PARAM,
                        f"input {item.filename!r} is not an accepted format for task {kind_id!r}",
                        None,
                    )
                content_type = item.content_type or "application/octet-stream"
                # REvoCompute reads the REPEATED `files` form field for every byte
                # input (its canonical client appends each part under `files`).
                files.append(("files", (item.filename, item.data, content_type)))
            elif item.external is not None:
                reference = self._artifact_reference(item.external)
                if reference is None:
                    raise self._error(
                        CapabilityErrorKind.INVALID_PARAM,
                        f"input authority {item.external.authority!r} cannot be referenced by REvoCompute",
                        None,
                    )
                artifact_refs.append(reference)
            else:
                raise self._error(
                    CapabilityErrorKind.INVALID_PARAM, "input carries neither bytes nor a reference", None
                )
        for reference in artifact_refs:
            # Each form field may contain newline-separated reference entries.
            data["artifact_references"] = "\n".join(artifact_refs)

        response = self._post_form("/compute/api/post", data, files, credentials)
        self._raise_on_error(response)
        task_id = self._extract_task_id(response)
        if task_id is None:
            raise self._error(
                CapabilityErrorKind.UNKNOWN, "submission did not return a task identity", response
            )
        return RunHandle(authority=REVOCOMPUTE_AUTHORITY, native_id=task_id, task_type=kind_id)

    @staticmethod
    def _form_value(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    @staticmethod
    def _artifact_reference(external: ExternalArtifactRef) -> str | None:
        """Map a previously stored REvoCompute artifact identity back to the
        ``@<md5sum>/<path>`` reference grammar. Unknown authorities/identities
        return None so the caller fails with a typed INVALID_PARAM."""
        if external.authority != REVOCOMPUTE_AUTHORITY:
            return None
        raw_task_id, separator, path = external.native_id.partition(":")
        task_id = _sanitize_task_id(raw_task_id)
        if not separator or task_id is None or not path:
            return None
        return f"@{task_id}/{path}"

    # -- run state / artifacts -------------------------------------------------------

    def get_run(self, native_id: str, credentials: CredentialLease) -> RunView:
        task_id = self._normalize_owned_task_id(native_id)
        response = self._request("GET", f"/compute/api/running/{task_id}", credentials)
        body = self._json_body(response)
        # REvoCompute reports a failed run as HTTP 404 with {"status":"failed"} —
        # distinguish that from a genuinely unknown task.
        if response.status_code == 404 and isinstance(body, dict) and body.get("status") == "failed":
            return RunView(
                authority=REVOCOMPUTE_AUTHORITY,
                native_id=task_id,
                status="failed",
                status_detail=body.get("error"),
            )
        self._raise_on_error(response)
        if not isinstance(body, dict) or "status" not in body:
            raise self._error(CapabilityErrorKind.UNKNOWN, "unexpected run-state payload", response)
        return RunView(
            authority=REVOCOMPUTE_AUTHORITY,
            native_id=task_id,
            status=str(body["status"]),
            status_detail=body.get("error"),
        )

    def list_artifacts(self, native_id: str, credentials: CredentialLease) -> list[ArtifactHandle]:
        task_id = self._normalize_owned_task_id(native_id)
        response = self._request("GET", f"/compute/api/results/{task_id}", credentials)
        if response.status_code in _REDIRECT_STATUSES:
            # REvoCompute redirects to the running endpoint while the run is not
            # finished: there is no artifact manifest to enumerate yet.
            return []
        self._raise_on_error(response)
        body = self._json_body(response)
        if not isinstance(body, dict) or "artifacts" not in body:
            raise self._error(CapabilityErrorKind.UNKNOWN, "unexpected results payload", response)
        handles: list[ArtifactHandle] = []
        for artifact in body.get("artifacts") or []:
            if not isinstance(artifact, dict):
                continue
            path = str(artifact.get("path", ""))
            if not path:
                continue
            handles.append(
                ArtifactHandle(
                    authority=REVOCOMPUTE_AUTHORITY,
                    native_id=f"{task_id}:{path}",
                    version_id="",
                    content_type=artifact.get("media_type"),
                    size=artifact.get("size"),
                    checksum=artifact.get("sha256"),
                )
            )
        return handles

    # -- transport plumbing -----------------------------------------------------------

    def _post_form(
        self,
        path: str,
        data: Mapping[str, Any],
        files: Sequence[Any],
        credentials: CredentialLease,
    ) -> httpx.Response:
        return self._httpx_call(
            lambda: self._client.post(
                path, data=dict(data), files=list(files), headers=self._headers(credentials)
            )
        )

    def _request(self, method: str, path: str, credentials: CredentialLease) -> httpx.Response:
        return self._httpx_call(
            lambda: self._client.request(method, path, headers=self._headers(credentials))
        )

    def _request_json(
        self, method: str, path: str, credentials: CredentialLease
    ) -> Mapping[str, Any]:
        response = self._request(method, path, credentials)
        self._raise_on_error(response)
        body = self._json_body(response)
        if not isinstance(body, Mapping):
            raise self._error(CapabilityErrorKind.UNKNOWN, "unexpected upstream payload", response)
        return body

    @staticmethod
    def _headers(credentials: CredentialLease) -> dict[str, str]:
        # The single long-lived credential is presented as the X-API-Key header.
        # The lease material is the ONLY path secret bytes take toward the wire.
        return {"X-API-Key": credentials.get(REVOCOMPUTE_CREDENTIAL_KIND)}

    @staticmethod
    def _httpx_call(build: Callable[[], httpx.Response]) -> httpx.Response:
        try:
            return build()
        except httpx.TimeoutException as exc:
            raise CapabilityError(
                CapabilityErrorKind.NETWORK,
                "REvoCompute request timed out",
                provider_key=_PROVIDER_KEY,
                capability_kind=CapabilityKind.COMPUTE,
                retryable=True,
            ) from exc
        except httpx.TransportError as exc:
            raise CapabilityError(
                CapabilityErrorKind.NETWORK,
                "REvoCompute is unreachable",
                provider_key=_PROVIDER_KEY,
                capability_kind=CapabilityKind.COMPUTE,
                retryable=True,
            ) from exc

    def _json_body(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise self._error(CapabilityErrorKind.UNKNOWN, "non-JSON upstream response", response) from exc

    def _raise_on_error(self, response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300 or status in _REDIRECT_STATUSES:
            return
        if status in {401, 403}:
            raise self._error(CapabilityErrorKind.AUTH, "REvoCompute rejected the credential", response)
        if status == 404:
            raise self._error(CapabilityErrorKind.NOT_FOUND, "REvoCompute identity not found", response)
        if status in {400, 422}:
            raise self._error(CapabilityErrorKind.INVALID_PARAM, "REvoCompute rejected the parameters", response)
        if status == 409:
            raise self._error(CapabilityErrorKind.INVALID_PARAM, "REvoCompute request conflicted", response)
        if status in {429, 502, 503, 504} or status >= 500:
            raise self._error(
                CapabilityErrorKind.PROVIDER_UNAVAILABLE,
                "REvoCompute is temporarily unavailable",
                response,
                retryable=True,
            )
        raise self._error(CapabilityErrorKind.UNKNOWN, f"unexpected upstream status {status}", response)

    def _error(
        self,
        kind: CapabilityErrorKind,
        message: str,
        response: httpx.Response | None,
        *,
        retryable: bool = False,
    ) -> CapabilityError:
        return CapabilityError(
            kind,
            message,
            provider_key=_PROVIDER_KEY,
            capability_kind=CapabilityKind.COMPUTE,
            retryable=retryable,
            upstream_status=response.status_code if response is not None else None,
        )

    def _normalize_owned_task_id(self, native_id: str) -> str:
        task_id = _sanitize_task_id(native_id)
        if task_id is None:
            raise self._error(CapabilityErrorKind.INVALID_PARAM, "invalid REvoCompute task id", None)
        return task_id

    def _extract_task_id(self, response: httpx.Response) -> str | None:
        body = self._json_body(response) if response.headers.get("content-type", "").startswith(
            "application/json"
        ) else None
        if isinstance(body, dict):
            candidate = body.get("md5sum")
            normalized = _sanitize_task_id(candidate)
            if normalized:
                return normalized
        # The success/finished path redirects to /compute/api/running/<md5sum>.
        path = str(response.url.path)
        if "/running/" in path:
            normalized = _sanitize_task_id(path.rsplit("/running/", 1)[-1])
            if normalized:
                return normalized
        location = response.headers.get("Location")
        if location and "/running/" in location and _same_origin(self._base_url, location):
            normalized = _sanitize_task_id(location.rsplit("/running/", 1)[-1])
            if normalized:
                return normalized
        return None


class REvoComputeArtifactResolutionCapability:
    """`CapabilityKind.ARTIFACT_RESOLUTION` realization over REvoCompute."""

    provider_key = _PROVIDER_KEY
    kind = CapabilityKind.ARTIFACT_RESOLUTION

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def resolve(self, artifact: ExternalArtifactRef, credentials: CredentialLease) -> ArtifactHandle:
        task_id, path = self._parse_identity(artifact)
        response = self._httpx_call(
            lambda: self._client.get(
                f"/compute/api/results/{task_id}/artifacts/{quote(path, safe='/')}",
                headers={"X-API-Key": credentials.get(REVOCOMPUTE_CREDENTIAL_KIND)},
            )
        )
        self._raise_on_error(response)
        data = response.content
        digest = hashlib.sha256(data).hexdigest()
        if (artifact.checksum and digest != artifact.checksum) or (
            artifact.size is not None and len(data) != artifact.size
        ):
            raise CapabilityError(
                CapabilityErrorKind.UNKNOWN,
                "artifact bytes failed the recorded integrity check",
                provider_key=_PROVIDER_KEY,
                capability_kind=CapabilityKind.ARTIFACT_RESOLUTION,
                upstream_status=response.status_code,
            )
        return ArtifactHandle(
            authority=REVOCOMPUTE_AUTHORITY,
            native_id=f"{task_id}:{path}",
            version_id="",
            content_type=artifact.content_type,
            size=len(data),
            checksum=digest,
            data=data,
        )

    @staticmethod
    def _parse_identity(artifact: ExternalArtifactRef) -> tuple[str, str]:
        if artifact.authority != REVOCOMPUTE_AUTHORITY:
            raise CapabilityError(
                CapabilityErrorKind.INVALID_PARAM,
                f"cannot resolve authority {artifact.authority!r} through REvoCompute",
                provider_key=_PROVIDER_KEY,
                capability_kind=CapabilityKind.ARTIFACT_RESOLUTION,
            )
        task_id, separator, path = artifact.native_id.partition(":")
        normalized = _sanitize_task_id(task_id)
        if not separator or normalized is None or not path:
            raise CapabilityError(
                CapabilityErrorKind.INVALID_PARAM,
                "invalid REvoCompute artifact identity",
                provider_key=_PROVIDER_KEY,
                capability_kind=CapabilityKind.ARTIFACT_RESOLUTION,
            )
        if path.startswith("/") or ".." in path.split("/") or "\\" in path:
            raise CapabilityError(
                CapabilityErrorKind.INVALID_PARAM,
                "invalid REvoCompute artifact path",
                provider_key=_PROVIDER_KEY,
                capability_kind=CapabilityKind.ARTIFACT_RESOLUTION,
            )
        return normalized, path

    def _raise_on_error(self, response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if status in {401, 403}:
            raise self._error(CapabilityErrorKind.AUTH, "REvoCompute rejected the credential", response)
        if status == 404:
            raise self._error(CapabilityErrorKind.NOT_FOUND, "artifact not found in REvoCompute", response)
        if status in {400, 422}:
            raise self._error(CapabilityErrorKind.INVALID_PARAM, "REvoCompute rejected the request", response)
        if status >= 500 or status == 429:
            raise self._error(
                CapabilityErrorKind.PROVIDER_UNAVAILABLE,
                "REvoCompute is temporarily unavailable",
                response,
                retryable=True,
            )
        raise self._error(CapabilityErrorKind.UNKNOWN, f"unexpected upstream status {status}", response)

    def _error(
        self,
        kind: CapabilityErrorKind,
        message: str,
        response: httpx.Response,
        *,
        retryable: bool = False,
    ) -> CapabilityError:
        return CapabilityError(
            kind,
            message,
            provider_key=_PROVIDER_KEY,
            capability_kind=CapabilityKind.ARTIFACT_RESOLUTION,
            retryable=retryable,
            upstream_status=response.status_code,
        )

    @staticmethod
    def _httpx_call(build: Callable[[], httpx.Response]) -> httpx.Response:
        try:
            return build()
        except httpx.TimeoutException as exc:
            raise CapabilityError(
                CapabilityErrorKind.NETWORK,
                "REvoCompute request timed out",
                provider_key=_PROVIDER_KEY,
                capability_kind=CapabilityKind.ARTIFACT_RESOLUTION,
                retryable=True,
            ) from exc
        except httpx.TransportError as exc:
            raise CapabilityError(
                CapabilityErrorKind.NETWORK,
                "REvoCompute is unreachable",
                provider_key=_PROVIDER_KEY,
                capability_kind=CapabilityKind.ARTIFACT_RESOLUTION,
                retryable=True,
            ) from exc


class REvoComputeDriver:
    """REvoCompute driver: one driver realizing COMPUTE + ARTIFACT_RESOLUTION."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self.name = _PROVIDER_KEY
        self.display_name = "REvoCompute"
        self.description: str | None = "External compute engine for structural and systems simulation tasks."
        # Durable identity authority this driver resolves. Declared explicitly so
        # Core never assumes authority == provider key.
        self.authorities: tuple[str, ...] = (REVOCOMPUTE_AUTHORITY,)
        self.required_credential_kinds: tuple[str, ...] = (REVOCOMPUTE_CREDENTIAL_KIND,)
        self._transport = transport
        self._base_url = ""
        self._timeout = 30.0
        self._client: httpx.Client | None = None
        self.capabilities: Mapping[CapabilityKind, Any] = {}

    def start(self, context: DriverContext) -> None:
        base_url = str(context.settings.get("revocompute_base_url", "")).rstrip("/")
        if not base_url:
            raise RuntimeError(
                "REvoCompute driver requires the 'revocompute_base_url' setting"
            )
        self._base_url = base_url
        self._timeout = float(context.settings.get("revocompute_timeout_seconds", 30.0))
        # Follow redirects is OFF: the credential is a custom header that httpx
        # would replay cross-origin. The driver reads the 302 Location itself and
        # only accepts same-origin targets (see `_extract_task_id`).
        self._client = httpx.Client(
            base_url=base_url,
            timeout=self._timeout,
            follow_redirects=False,
            transport=self._transport,
        )
        self.capabilities = {
            CapabilityKind.COMPUTE: REvoComputeComputeCapability(self._client, base_url),
            CapabilityKind.ARTIFACT_RESOLUTION: REvoComputeArtifactResolutionCapability(self._client),
        }

    def stop(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        self.capabilities = {}

    def probe_health(self) -> ProviderRuntimeHealth:
        if self._client is None:
            return ProviderRuntimeHealth.UNREACHABLE
        try:
            response = self._client.get("/compute/health", timeout=min(self._timeout, 10.0))
        except httpx.TransportError:
            return ProviderRuntimeHealth.UNREACHABLE
        return ProviderRuntimeHealth.READY if 200 <= response.status_code < 300 else ProviderRuntimeHealth.DEGRADED


__all__ = [
    "REVOCOMPUTE_AUTHORITY",
    "REVOCOMPUTE_CREDENTIAL_KIND",
    "REvoComputeDriver",
]
