"""Host-control and capture interfaces. None of these DTOs confer host authority on import."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from nexus_memory.domain.graph import CurationBatch, Span
from nexus_memory.domain.models import MemoryInput, RepositoryBinding
from nexus_memory.domain.retrieval import AnchorProvider as AnchorProvider, RecallBudget, RecallQuery
from nexus_memory.domain.spaces import GrantContext, MemoryRef, OperationRef, SpaceBinding


@dataclass(frozen=True)
class HostCapabilities:
    """An explicit-only host advertises no automatic lifecycle behavior."""

    session_start: bool = False
    before_turn: bool = False
    tool_result: bool = False
    turn_end: bool = False
    compaction: bool = False
    stable_event_id: bool = False
    fork_cutoff: bool = False
    ordinary_data_injection: bool = False
    delivery_ack: bool = False


@dataclass(frozen=True)
class VerifiedCheckout:
    repository: RepositoryBinding
    resolved_commit: str


@dataclass(frozen=True)
class TrustedSessionContext:
    """Created by trusted registration, never decoded from a model request."""

    grant: GrantContext
    binding: SpaceBinding
    installation_id: str
    profile_id: str
    receiving_context_id: str
    capabilities: HostCapabilities
    checkout: VerifiedCheckout | None


@dataclass(frozen=True)
class EventSource:
    source_ref: str
    digest: str


@dataclass(frozen=True)
class HostEvent:
    schema_version: int
    installation_id: str
    profile_id: str
    origin_session_id: str
    receiving_context_id: str
    phase: str
    disposition: str
    timestamp: str
    native_event_id: str | None = None
    persisted_sequence: int | None = None
    logical_turn_id: str | None = None
    attempt_id: str | None = None
    parent_session_id: str | None = None
    fork_cutoff: str | None = None
    agent_id: str | None = None
    sources: tuple[EventSource, ...] = ()


@dataclass(frozen=True)
class ObserveReceipt:
    observation_id: str
    job_ids: tuple[str, ...]


@dataclass(frozen=True)
class PreparedTurn:
    pack_id: str
    allowance_id: str
    admitted_publication_id: str


DeliveryState = Literal['offered', 'admitted', 'transport_handoff', 'delivered', 'opened', 'cited']


@dataclass(frozen=True)
class DeliveryReceipt:
    receiving_context_id: str
    pack_id: str
    pack_digest: str
    native_ack_id: str
    state: DeliveryState
    exact_revision_ids: tuple[MemoryRef, ...]


class LifecycleService(Protocol):
    def capabilities(self) -> HostCapabilities: ...

    def observe(self, event: HostEvent, context: TrustedSessionContext) -> ObserveReceipt: ...

    def prepare_turn(self, query: RecallQuery, context: TrustedSessionContext, budget: RecallBudget) -> PreparedTurn: ...

    def ack_delivery(
        self, pack_id: str, exact_revision_ids: tuple[MemoryRef, ...], context: TrustedSessionContext,
        *, state: DeliveryState, native_ack_id: str,
    ) -> DeliveryReceipt: ...


@dataclass(frozen=True)
class ExtractionSource:
    event_id: str
    source: EventSource
    excerpt: str


@dataclass(frozen=True)
class CapturePolicy:
    mode: Literal['explicit', 'propose', 'automatic'] = 'explicit'
    destination_space_id: str | None = None
    allowed_kinds: tuple[str, ...] = ()
    allowed_sources: tuple[str, ...] = ()
    allowed_extractors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtractionDraft:
    memory: MemoryInput
    supporting_spans: tuple[Span, ...]
    curation: CurationBatch | None = None


@dataclass(frozen=True)
class ExtractionBatch:
    drafts: tuple[ExtractionDraft, ...]
    unresolved_entity_suggestions: tuple[str, ...]
    producer_version: str
    model_version: str | None


class Extractor(Protocol):
    def extract(self, source: ExtractionSource, policy: CapturePolicy) -> ExtractionBatch: ...


ProposalState = Literal['pending', 'accepted', 'rejected', 'superseded', 'expired']


@dataclass(frozen=True)
class ProposalReceipt:
    proposal_id: str
    revision_id: str
    state: ProposalState
    operation: OperationRef


class ProposalService(Protocol):
    def review(
        self, proposal_id: str, expected_revision_id: str, decision: Literal['accept', 'reject'],
        *, operation: OperationRef,
    ) -> ProposalReceipt: ...
