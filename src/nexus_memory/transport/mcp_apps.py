"""Opt-in SEP-1865 MCP App resources and metadata for interactive hosts like Antigravity."""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import asdict
from importlib import resources
from pathlib import Path
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from nexus_memory.domain.errors import NexusError
from nexus_memory.domain.models import SearchQuery
from nexus_memory.memory import MemoryService

Kind = Literal["observation", "decision", "constraint", "procedure", "failure"]

CARD_RESOURCE_URI = "ui://nexus-memory/card.html"
ATLAS_RESOURCE_URI = "ui://nexus-memory/atlas.html"
MCP_APP_MIME = "text/html;profile=mcp-app"

INLINE_CARD_META: dict[str, Any] = {"ui": {"resourceUri": CARD_RESOURCE_URI}}
ATLAS_PANE_META: dict[str, Any] = {
    "ui": {"resourceUri": ATLAS_RESOURCE_URI},
    "com.google.antigravity/ui": {
        "entrypoints": [
            {"type": "aux_pane", "title": "Nexus Atlas"},
            {"type": "global", "title": "Nexus Atlas"},
        ]
    },
}


class AtlasReferenceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository_id: str
    commit_oid: str
    path: str
    object_oid: str
    entry_type: str
    mode: str
    checked_at: str
    evidence: list[str]


class AtlasHitOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memory_id: str
    revision_id: str
    kind: str
    tags: list[str]
    created_at: str
    excerpt: str
    lexical_rank: float | None
    match_reasons: list[str]
    has_earlier_revisions: bool
    references: list[AtlasReferenceOutput]


class AtlasOverviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active_memories: int
    forgotten_memories: int
    revisions: int
    verification: str
    capture_policy: str
    hits: list[AtlasHitOutput]
    cursor: str | None
    generation: int


def load_app_html(name: Literal["card", "atlas"]) -> str:
    """Load bundled single-file MCP App HTML, honoring optional local override dir."""
    filename = f"{name}.html"
    override = os.environ.get("NEXUS_MCP_APPS_DIR")
    if override:
        override_path = Path(override) / filename
        if override_path.is_file():
            return override_path.read_text(encoding="utf-8")
    return resources.files("nexus_memory.transport.mcp_apps_static").joinpath(filename).read_text(encoding="utf-8")


def register_mcp_apps(
    server: MCPServer,
    service: MemoryService,
    *,
    capture_policy: str = "explicit",
) -> None:
    """Register ui:// HTML resources and the read-only open_atlas entrypoint tool."""

    @server.resource(
        CARD_RESOURCE_URI,
        name="Nexus Memory Inline Card",
        description="Interactive inline card UI for Nexus Memory status, record inspection, and HEAD ref checks.",
        mime_type=MCP_APP_MIME,
        meta={"ui": {"prefersBorder": True}},
    )
    def card_app_html() -> str:
        return load_app_html("card")

    @server.resource(
        ATLAS_RESOURCE_URI,
        name="Nexus Memory Atlas",
        description="Read-only Nexus Memory Atlas UI for side-pane and sidebar inspection.",
        mime_type=MCP_APP_MIME,
        meta={"ui": {"prefersBorder": False}},
    )
    def atlas_app_html() -> str:
        return load_app_html("atlas")

    def _safe_run(call: Callable[[], dict[str, Any]]) -> AtlasOverviewOutput | CallToolResult:
        try:
            return AtlasOverviewOutput.model_validate(call())
        except NexusError as error:
            return CallToolResult(
                content=[TextContent(type="text", text=f"{error.code}: {error}")],
                is_error=True,
            )
        except Exception:
            return CallToolResult(
                content=[TextContent(type="text", text="internal_error: operation failed")],
                is_error=True,
            )

    @server.tool(
        name="open_atlas",
        title="Nexus Atlas",
        description=(
            "Open the read-only Nexus Memory Atlas view for this bound project scope. "
            "All arguments are optional so empty launch returns recent active memories."
        ),
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        meta=ATLAS_PANE_META,
        structured_output=True,
    )
    def open_atlas(
        query: str | None = Field(default=None, max_length=1024, description="Optional literal search filter"),
        kind: Kind | None = Field(default=None, description="Optional memory kind filter"),
        limit: int = Field(default=20, ge=1, le=100),
    ) -> AtlasOverviewOutput:
        def load() -> dict[str, Any]:
            st = service.status()
            kinds = (kind,) if kind is not None else ()
            page = service.search(SearchQuery(query=query, kinds=kinds, limit=limit))
            return {
                "active_memories": st.active_memories,
                "forgotten_memories": st.forgotten_memories,
                "revisions": st.revisions,
                "verification": st.verification,
                "capture_policy": capture_policy,
                **asdict(page),
            }

        return _safe_run(load)
