"""In-process provider-neutral fake COMPUTE / ARTIFACT_RESOLUTION provider.

This exists ONLY for the browser vertical slice (`REVOLAB_E2E_FAKE_COMPUTE=1`)
and for Core/domain tests that must never contain REvoCompute vocabulary. It is
deliberately provider-neutral: task names, parameter names and status strings are
the fake's own, not REvoCompute's.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from revolab.capabilities import (
    ArtifactHandle,
    ExternalArtifactRef,
    InputSpec,
    ResolvedInput,
    RunHandle,
    RunView,
    TaskKindRef,
    TaskKindSchema,
)
from revolab.credentials import CredentialLease
from revolab.drivers import Capability, DriverContext
from revolab.enums import CapabilityKind, ProviderRuntimeHealth

FAKE_PROVIDER_KEY = "fakecompute"
FAKE_AUTHORITY = "fakecompute"


class _FakeComputeCapability:
    provider_key = FAKE_PROVIDER_KEY
    kind = CapabilityKind.COMPUTE

    def __init__(self, state: _FakeState) -> None:
        self._state = state

    def list_task_kinds(self, credentials: CredentialLease) -> list[TaskKindRef]:
        return [
            TaskKindRef(
                kind_id="echo",
                display_name="Echo (fake)",
                description="Synthetic task that echoes its inputs into one text artifact.",
                category="synthetic",
            ),
            TaskKindRef(
                kind_id="tabular",
                display_name="Tabular (fake)",
                description="Synthetic task that emits a small deterministic CSV artifact.",
                category="synthetic",
            ),
        ]

    def task_kind_schema(self, kind_id: str, credentials: CredentialLease) -> TaskKindSchema:
        if kind_id == "tabular":
            return TaskKindSchema(
                kind_id="tabular",
                display_name="Tabular (fake)",
                description="Synthetic task that emits a small deterministic CSV artifact.",
                parameter_schema={
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": {
                        "rows": {"type": "integer", "title": "Rows", "default": 5, "minimum": 1, "maximum": 20},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
                input_spec=InputSpec(label="Any input", required=False, multiple=True),
            )
        return TaskKindSchema(
            kind_id="echo",
            display_name="Echo (fake)",
            description="Synthetic task that echoes its inputs into one artifact.",
            parameter_schema={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {
                    "message": {"type": "string", "title": "Message", "default": "hello"},
                    "repetitions": {"type": "integer", "title": "Repetitions", "default": 1, "minimum": 1},
                },
                "required": [],
                "additionalProperties": False,
            },
            input_spec=InputSpec(label="Any input", required=False, multiple=True),
        )

    def submit(
        self,
        kind_id: str,
        inputs: Sequence[ResolvedInput],
        params: Mapping[str, Any],
        credentials: CredentialLease,
    ) -> RunHandle:
        native_id = self._state.submit(kind_id, inputs, params)
        return RunHandle(authority=FAKE_AUTHORITY, native_id=native_id, task_type=kind_id)

    def get_run(self, native_id: str, credentials: CredentialLease) -> RunView:
        return RunView(authority=FAKE_AUTHORITY, native_id=native_id, status="finished")

    def list_artifacts(self, native_id: str, credentials: CredentialLease) -> list[ArtifactHandle]:
        data, filename, content_type = self._state.artifact(native_id)
        return [
            ArtifactHandle(
                authority=FAKE_AUTHORITY,
                native_id=f"{native_id}:{filename}",
                version_id="",
                content_type=content_type,
                size=len(data),
                checksum=hashlib.sha256(data).hexdigest(),
            )
        ]


class _FakeArtifactResolutionCapability:
    provider_key = FAKE_PROVIDER_KEY
    kind = CapabilityKind.ARTIFACT_RESOLUTION

    def __init__(self, state: _FakeState) -> None:
        self._state = state

    def resolve(self, artifact: ExternalArtifactRef, credentials: CredentialLease) -> ArtifactHandle:
        run_id, _, _ = artifact.native_id.partition(":")
        data, _, content_type = self._state.artifact(run_id)
        return ArtifactHandle(
            authority=FAKE_AUTHORITY,
            native_id=artifact.native_id,
            version_id="",
            content_type=content_type,
            size=len(data),
            checksum=hashlib.sha256(data).hexdigest(),
            data=data,
        )

    def preview(
        self,
        artifact: ExternalArtifactRef,
        credentials: CredentialLease,
        *,
        offset: int = 0,
        limit: int,
    ) -> ArtifactHandle:
        run_id, _, _ = artifact.native_id.partition(":")
        data, _, content_type = self._state.artifact(run_id)
        head = data[offset : offset + limit]
        return ArtifactHandle(
            authority=FAKE_AUTHORITY,
            native_id=artifact.native_id,
            version_id="",
            content_type=content_type,
            size=len(data),
            checksum=hashlib.sha256(data).hexdigest(),
            data=head,
        )


class _FakeState:
    """In-memory run -> artifact bytes. Not persisted, not secret-bearing."""

    def __init__(self) -> None:
        self._runs: dict[str, tuple[bytes, str, str]] = {}

    def submit(
        self, kind_id: str, inputs: Sequence[ResolvedInput], params: Mapping[str, Any]
    ) -> str:
        native_id = f"fake-{uuid4().hex}"
        if kind_id == "tabular":
            rows = max(1, min(int(params.get("rows", 5)), 20))
            lines = ["group,measure,value"]
            for i in range(rows):
                lines.append(f"g{i % 2},m{i},{i * 2.5}")
            data = ("\n".join(lines) + "\n").encode()
            self._runs[native_id] = (data, "table.csv", "text/csv")
            return native_id
        message = str(params.get("message", "hello"))
        repetitions = int(params.get("repetitions", 1))
        input_note = ";".join(
            f"{item.filename}:{len(item.data) if item.data is not None else item.external}"
            for item in inputs
        )
        data = (f"{message}\n" * max(repetitions, 1) + f"inputs={input_note}\n").encode()
        self._runs[native_id] = (data, "echo.txt", "text/plain")
        return native_id

    def artifact(self, run_id: str) -> tuple[bytes, str, str]:
        return self._runs.get(run_id, (b"", "echo.txt", "text/plain"))


class FakeComputeDriver:
    """A zero-credential provider driver for the browser vertical slice."""

    def __init__(self) -> None:
        self.name = FAKE_PROVIDER_KEY
        self.display_name = "Fake Compute (in-process)"
        self.description: str | None = "Synthetic in-process compute provider for vertical-slice testing."
        self.authorities: tuple[str, ...] = (FAKE_AUTHORITY,)
        self.required_credential_kinds: tuple[str, ...] = ()
        self._state = _FakeState()
        self.capabilities: Mapping[CapabilityKind, Capability] = {
            CapabilityKind.COMPUTE: _FakeComputeCapability(self._state),
            CapabilityKind.ARTIFACT_RESOLUTION: _FakeArtifactResolutionCapability(self._state),
        }

    def start(self, context: DriverContext) -> None:
        pass

    def stop(self) -> None:
        pass

    def probe_health(self) -> ProviderRuntimeHealth:
        return ProviderRuntimeHealth.READY


__all__ = ["FAKE_AUTHORITY", "FAKE_PROVIDER_KEY", "FakeComputeDriver"]
