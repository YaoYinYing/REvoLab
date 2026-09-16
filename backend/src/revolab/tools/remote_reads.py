"""Remote READ-ONLY provider Tool dispatch (Phase 13 literature, Phase 14 protein).

A few provider capabilities are safe to cross autonomously: a bounded, read-only,
credential-light remote lookup whose result is untrusted data and whose invocation
persists nothing. The Agent loop may execute exactly those, and only those.

This module is the ONE place that links a remote read-only Tool id to the
application service it invokes, and it does so by the stable capability SUFFIX
(`.literature.search`, `.protein.search`), never by the provider key prefix — the
prefix is dynamic provider data, and Core must not branch on provider vocabulary. It
is the same single-sourcing pattern as `revolab.tools.explicit_actions` for remote
explicit actions.

A Tool is Agent-executable as a remote read ONLY when it is both listed here AND
projected by the catalog with `autonomy=automatic` and
`side_effect_class=read_only`. Everything else remote still fails closed.

The handler receives the Tool descriptor's provider key, so the provider is
resolved from the canonical Tool id rather than from model-supplied arguments.

Phase 14 deliberately registers ONLY the read-only protein SEARCH: there is no
Agent-reachable import Tool, so an external candidate can never become Project truth
without an explicit human Import.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from revolab.enums import ToolResultKind
from revolab.literature import discover_literature
from revolab.proteins import discover_proteins
from revolab.schemas import LiteratureSearchToolInput, ProteinSearchToolInput
from revolab.tools.types import HandlerOutput, InvocationContext

LITERATURE_SEARCH_SUFFIX = ".literature.search"
PROTEIN_SEARCH_SUFFIX = ".protein.search"

RemoteReadHandler = Callable[[InvocationContext, BaseModel, str], HandlerOutput]


def _literature_search(
    ctx: InvocationContext, parsed: BaseModel, provider_key: str
) -> HandlerOutput:
    """The SAME application service the human Literature workspace calls.

    Read-only: it writes nothing, changes no ContextSelection, creates no
    LiteratureReference/Evidence/Decision/ActionRequest, and never resolves
    artifact bytes. A model-supplied provider endpoint is structurally impossible —
    the provider comes from the Tool descriptor.
    """
    if not isinstance(parsed, LiteratureSearchToolInput):  # pragma: no cover - wiring
        raise TypeError("internal tool wiring error: expected LiteratureSearchToolInput")
    result = discover_literature(
        ctx.session,
        ctx.registry,
        ctx.secret_store,
        ctx.actor_id,
        ctx.project_id,
        provider_key=provider_key,
        query=parsed.query,
        limit=parsed.limit,
    )
    return HandlerOutput(kind=ToolResultKind.EPHEMERAL, value=result)


def _protein_search(
    ctx: InvocationContext, parsed: BaseModel, provider_key: str
) -> HandlerOutput:
    """The SAME application service the human Objects workspace calls.

    Read-only: it writes nothing, changes no ContextSelection, and creates no
    ExternalIdentity/ExternalReference/ScientificObject/Evidence/Decision/
    ActionRequest. The candidate projection is bounded untrusted data with no
    sequence, and the Tool has no import capability at all.
    """
    if not isinstance(parsed, ProteinSearchToolInput):  # pragma: no cover - wiring
        raise TypeError("internal tool wiring error: expected ProteinSearchToolInput")
    result = discover_proteins(
        ctx.session,
        ctx.registry,
        ctx.secret_store,
        ctx.actor_id,
        ctx.project_id,
        provider_key=provider_key,
        query=parsed.query,
        limit=parsed.limit,
    )
    return HandlerOutput(kind=ToolResultKind.EPHEMERAL, value=result)


# Remote read-only Tool suffix -> (canonical input model, handler). The provider
# key prefix is taken from the live catalog descriptor, never parsed from the id.
REMOTE_READS: dict[str, tuple[type[BaseModel], RemoteReadHandler]] = {
    LITERATURE_SEARCH_SUFFIX: (LiteratureSearchToolInput, _literature_search),
    PROTEIN_SEARCH_SUFFIX: (ProteinSearchToolInput, _protein_search),
}


def remote_read_spec(tool_id: str) -> tuple[type[BaseModel], RemoteReadHandler] | None:
    """The canonical (input model, handler) for a remote read-only Tool id, or
    None when the id is not an Agent-executable remote read (fail closed)."""
    for suffix, spec in REMOTE_READS.items():
        if tool_id.endswith(suffix):
            return spec
    return None


def is_remote_read_tool(tool_id: str) -> bool:
    return remote_read_spec(tool_id) is not None


__all__ = [
    "LITERATURE_SEARCH_SUFFIX",
    "PROTEIN_SEARCH_SUFFIX",
    "REMOTE_READS",
    "RemoteReadHandler",
    "is_remote_read_tool",
    "remote_read_spec",
]
