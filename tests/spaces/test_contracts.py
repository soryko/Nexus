"""A01 — frozen shared contracts and safe error serialization (spec §3, §6-§10).

Named behavior tests required by the plan:

- ``test_untrusted_json_cannot_construct_authority``
- ``test_ids_and_utf8_bounds_preserve_legacy_contract``
- ``test_budget_defaults_and_caps``
"""

from __future__ import annotations

import hashlib
import json

import pytest

from nexus_memory.domain.errors import InvalidInput, NexusError
from nexus_memory.domain.graph import (
    decode_endpoint, EntityRef, LiteralRef, AssertionRevisionRef,
    EntityDraft, CurationBatch, AssertionDraft, MemorySource, Span, MAX_SPAN_BYTES,
    EvidenceInput,
    GraphReadSnapshot,
    GraphRepository,
    GraphService,
    Validity,
)
from dataclasses import replace
from nexus_memory.domain.models import MemoryInput, OBJECT_FORMATS, REFERENCE_OID, RepositoryBinding, Scope
from nexus_memory.domain.retrieval import (
    BUDGET_LIMITS,
    EvidencePack,
    PackSnapshot,
    RecallBudget,
    RecallService,
    TokenCounter,
    TraverseQuery,
)
from nexus_memory.domain.spaces import (
    MAX_OPERATION_KEY_CHARS,
    GrantContext,
    JobAuthorityContext,
    MemoryRef,
    Mount,
    OperationRef,
    PublicationCommand,
    SpaceBinding,
    canonical_command_digest,
)
from nexus_memory.lifecycle.contracts import AnchorProvider, Extractor, LifecycleService, TrustedSessionContext
from nexus_memory.memory.service import MemoryService
from nexus_memory.transport.mcp_server import _safe_error_text


def test_untrusted_json_cannot_construct_authority() -> None:
    """Model-facing schemas reject unknown path/principal/grant fields before values are trusted."""
    with pytest.raises(InvalidInput):
        OperationRef.from_json({"writer_stream_id": "s", "operation_key": "k", "path": "/etc/passwd"})
    with pytest.raises(InvalidInput):
        MemoryRef.from_json({"space_id": "s", "memory_id": "m", "revision_id": "r", "grant": "g"})
    with pytest.raises(InvalidInput):
        Mount.from_json({"alias": "a", "space_id": "s", "mode": "read_write", "default_write": True, "principal": "o"})
    with pytest.raises(InvalidInput):
        SpaceBinding.from_json({
            "scope": {"namespace": "n", "actor": "a"},
            "store_id": "st",
            "space_id": "sp",
            "locator": "/tmp/private/checkout",
        })
    command = {
        "input": {"content": "panel notes", "kind": "decision", "tags": [], "source_uri": None, "snapshot": None, "references": []},
        "source_refs": [{"space_id": "sp", "memory_id": "m", "revision_id": "r"}],
        "destination_space_id": "sp2",
        "independent_copy": False,
        "grant": {"epoch": 1},
    }
    with pytest.raises(InvalidInput):
        PublicationCommand.from_json(command)

    # Authority DTOs exist for trusted code paths only: no model-facing decode may exist.
    for authority in (GrantContext, JobAuthorityContext, TrustedSessionContext):
        assert not hasattr(authority, "from_json")


def test_bind_serialization_has_no_locator() -> None:
    """SpaceBinding serialization records the scope partition, no filesystem locator."""
    binding = SpaceBinding(
        space_id="sp-1",
        store_id="store-1",
        scope=Scope("space:sp-1", "space"),
        repository_bindings=(RepositoryBinding("repo-1", "sha1", "/Users/soko/private/checkout"),),
    )
    assert "locator" not in binding.to_json()
    assert "/Users" not in binding.to_json()
    restored = SpaceBinding.from_json(binding.to_json())
    assert restored == SpaceBinding("sp-1", "store-1", binding.scope, (RepositoryBinding("repo-1", "sha1", None),))


def test_ids_and_utf8_bounds_preserve_legacy_contract() -> None:
    """65,536-byte content, 128-character operation key and SHA-1/SHA-256 rules stay unchanged."""
    assert MemoryService._key("k" * 128) == "k" * 128
    with pytest.raises(InvalidInput):
        MemoryService._key("k" * 129)
    exact = 'é' * 32768
    assert MemoryInput(exact).content == exact
    with pytest.raises(InvalidInput):
        MemoryInput(exact + 'x')
    assert MemoryService.DIGEST_VERSION == 1
    assert MemoryService.REFERENCE_DIGEST_VERSION == 2
    assert MAX_OPERATION_KEY_CHARS == 128
    OperationRef("stream-1", "k" * 128)
    with pytest.raises(InvalidInput):
        OperationRef("stream-1", "k" * 129)
    assert OBJECT_FORMATS == {"sha1": 40, "sha256": 64}
    assert REFERENCE_OID.fullmatch("a" * 40)
    assert REFERENCE_OID.fullmatch("b" * 64)
    assert REFERENCE_OID.fullmatch("a" * 39) is None
    assert REFERENCE_OID.fullmatch("a" * 65) is None
    assert REFERENCE_OID.fullmatch("A" * 40) is None


def test_canonical_request_digest_rules() -> None:
    """Sorted-key UTF-8 JSON with no NaN/Infinity; schema version and operation kind included."""
    digest = canonical_command_digest(1, "evict", {"a": 2, "b": 1})
    expected = hashlib.sha256(b'{"command":{"a":2,"b":1},"operation_kind":"evict","schema_version":1}').hexdigest()
    assert digest == expected
    assert digest != canonical_command_digest(1, "record", {"a": 2, "b": 1})
    assert digest != canonical_command_digest(2, "evict", {"a": 2, "b": 1})
    with pytest.raises(InvalidInput):
        canonical_command_digest(1, "evict", {"x": float("nan")})
    with pytest.raises(InvalidInput):
        canonical_command_digest(True, "evict", {"x": 1})


def test_budget_defaults_and_caps() -> None:
    """Retrieval budget defaults, hard ceilings, rejection rules; validity kinds distinct."""
    budget = RecallBudget()
    assert (budget.seeds, budget.semantic_depth, budget.beam_width, budget.edges_per_endpoint) == (8, 2, 16, 16)
    assert (budget.expansion_rows, budget.proof_premise_rows, budget.conflict_rows) == (200, 256, 128)
    assert (budget.candidate_pool, budget.finalist_bundles) == (32, 8)
    assert (budget.query_deadline_ms, budget.execution_deadline_ms) == (250, 500)
    assert (budget.token_target, budget.serialized_bytes) == (1600, 6400)
    assert dict(BUDGET_LIMITS) == {
        "seeds": (8, 32),
        "semantic_depth": (2, 5),
        "beam_width": (16, 64),
        "alternative_states": (3, 8),
        "edges_per_endpoint": (16, 64),
        "expansion_rows": (200, 2000),
        "proof_premise_rows": (256, 2048),
        "conflict_rows": (128, 1024),
        "candidate_pool": (32, 128),
        "finalist_bundles": (8, 32),
        "query_deadline_ms": (250, 2000),
        "execution_deadline_ms": (500, 4000),
        "token_target": (1600, 8192),
        "serialized_bytes": (6400, 32768),
    }
    from nexus_memory.domain.errors import InvalidRetrievalBudget
    for field, (initial, ceiling) in BUDGET_LIMITS.items():
        for invalid in (True, -1, ceiling + 1, '8'):
            with pytest.raises(InvalidRetrievalBudget):
                RecallBudget(**{field: invalid})
        assert getattr(RecallBudget(**{field: ceiling}), field) == ceiling
        assert getattr(RecallBudget(**{field: initial}), field) == initial
    # ``unknown`` validity must never be equal to an explicit ``unbounded`` claim.
    assert Validity.unknown() != Validity.unbounded()
    assert Validity("unknown") == Validity.unknown()
    with pytest.raises(InvalidInput):
        Validity("interval", "2026-01-01T00:00:00Z", None)
    with pytest.raises(InvalidInput):
        Validity("unknown", "2026-01-01T00:00:00Z", None)


def test_safe_error_codes_serialize_without_leaking_fields() -> None:
    """Every spec §3 safe code is a direct NexusError subclass the transport admits."""
    from nexus_memory.domain.errors import (
        AmbiguousEntity,
        AuthorityBusy,
        EventIdentityConflict,
        EvidenceTooLarge,
        FlowDenied,
        GrantRevoked,
        InvalidEvidence,
        InvalidGraphPattern,
        InvalidRelation,
        InvalidRetrievalBudget,
        PublicationBusy,
        PublicationUnknown,
        ResolutionConflict,
        SpaceNotMounted,
        SpaceReadOnly,
        StalePack,
        UnsupportedCapability,
    )

    spec_codes = {
        SpaceNotMounted: "space_not_mounted",
        SpaceReadOnly: "space_read_only",
        GrantRevoked: "grant_revoked",
        AuthorityBusy: "authority_busy",
        PublicationBusy: "publication_busy",
        PublicationUnknown: "publication_unknown",
        FlowDenied: "flow_denied",
        AmbiguousEntity: "ambiguous_entity",
        InvalidRelation: "invalid_relation",
        InvalidEvidence: "invalid_evidence",
        UnsupportedCapability: "unsupported_capability",
        StalePack: "stale_pack",
        ResolutionConflict: "resolution_conflict",
        EventIdentityConflict: "event_identity_conflict",
        EvidenceTooLarge: "evidence_too_large",
        InvalidGraphPattern: "invalid_graph_pattern",
        InvalidRetrievalBudget: "invalid_retrieval_budget",
    }
    for error_type, code in spec_codes.items():
        assert error_type.code == code
        assert error_type.__mro__[1] is NexusError, code
        from nexus_memory.transport.mcp_server import _error
        text = _error(code, '/private/unmounted-canary').content[0].text
        assert text.startswith(f'{code}:') and 'canary' not in text
        assert _safe_error_text(text)
        assert not _safe_error_text(f'{code}: /private/unmounted-canary')
    assert _safe_error_text("internal_error: x")
    assert not _safe_error_text("path:/Users/soko/private")
    assert not _safe_error_text("space_not_mounted")


def test_service_protocols_are_declared_now() -> None:
    """Named protocols exist now so downstream signatures cannot drift (plan A01)."""
    for name in ("curate", "correct", "supersede", "resolve"):
        assert hasattr(GraphService, name)
    for name in ("recall", "traverse", "explain"):
        assert hasattr(RecallService, name)
    for name in ("capabilities", "observe", "prepare_turn", "ack_delivery"):
        assert hasattr(LifecycleService, name)
    assert hasattr(GraphRepository, "open_snapshot")
    for method in ("assertion", "support_groups", "evidence", "adjacent", "control_relations"):
        assert hasattr(GraphReadSnapshot, method)
    assert hasattr(AnchorProvider, "search")
    assert hasattr(Extractor, "extract")
    assert hasattr(TokenCounter, "count")


def test_endpoint_tagged_union_rejects_other_shapes() -> None:
    """Exactly one endpoint shape decodes; unknown kinds and extra fields fail closed."""
    for payload, expected in (
        ({"kind": "entity", "entity_id": "e1"}, EntityRef("e1")),
        ({"kind": "literal", "datatype": "xsd:string", "canonical_value": "v"}, LiteralRef("xsd:string", "v")),
        ({"kind": "assertion_revision", "assertion_id": "a1", "revision_id": "r1"}, AssertionRevisionRef("a1", "r1")),
    ):
        assert decode_endpoint(payload) == expected
        assert decode_endpoint(json.loads(expected.to_json())) == expected
    with pytest.raises(InvalidInput):
        decode_endpoint({"kind": "entity", "entity_id": "e1", "path": "/tmp"})
    with pytest.raises(InvalidInput):
        decode_endpoint({"kind": "relation", "relation_id": "x"})
    with pytest.raises(InvalidInput):
        decode_endpoint("entity")


def test_curation_batch_caps() -> None:
    """64 entities/assertions, 128 evidence rows, <=1MiB bytes, 8 support groups x 16 premises."""
    entities = tuple(EntityDraft(f"t{i}", "document", f"repo:lang:{i}") for i in range(64))
    batch = CurationBatch(entities=entities)
    assert len(batch.entities) == 64
    with pytest.raises(InvalidInput):
        CurationBatch(entities=tuple(EntityDraft(f"t{i}", "document", f"repo:lang:{i}") for i in range(65)))
    with pytest.raises(InvalidInput):
        AssertionDraft(
            subject=EntityRef("t0"),
            predicate="IMPLEMENTS",
            object=EntityRef("t1"),
            support_groups=tuple(tuple(f"p{j}" for j in range(17)) for _ in range(8)),
        )
    with pytest.raises(InvalidInput):
        AssertionDraft(
            subject=EntityRef("t0"),
            predicate="IMPLEMENTS",
            object=EntityRef("t1"),
            support_groups=tuple((f"p{j}",) for j in range(9)),
        )
    with pytest.raises(InvalidInput):
        EvidenceInput(target="a1", role="unknown", source=MemorySource(MemoryRef("sp", "m", "r")), span=Span(0, 2, "a" * 64))
    with pytest.raises(InvalidInput):
        EvidenceInput(target="a1", role="support", source=MemorySource(MemoryRef("sp", "m", "r")), span=Span(2, 2, "a" * 64))
    with pytest.raises(InvalidInput):
        Span(0, MAX_SPAN_BYTES + 1, "a" * 64)
    span = Span(0, MAX_SPAN_BYTES, "a" * 64)
    assert span.end == 65536


def test_curation_batch_byte_ceiling() -> None:
    """A canonical batch over 1 MiB is refused at construction."""
    big = EntityDraft("big", "document", "repo:lang:" + "x" * (1024 * 1024))
    with pytest.raises(InvalidInput):
        CurationBatch(entities=(big,))


def test_publication_command_roundtrip() -> None:
    """PublicationCommand keeps exact output bytes; decode is strict and bounds-preserving."""
    command = PublicationCommand(
        input=MemoryInput("panel decision", kind="decision", tags=("a",)),
        source_refs=(MemoryRef("sp", "m", "r"),),
        destination_space_id="sp-2",
        independent_copy=False,
    )
    restored = PublicationCommand.from_json(command.to_json())
    assert restored == command
    assert restored.input.content == "panel decision"


def test_evidence_pack_roundtrip() -> None:
    """EvidencePack serializes deterministically and rejects unknown fields."""
    pack = EvidencePack(
        schema_version=1,
        pack_id="pack-1",
        query_digest="a" * 64,
        context_digest="c" * 64,
        snapshots=(PackSnapshot("store-1", 1, "b" * 64, "v1"),),
        status="no_eligible_matches",
        limitations=(),
        bundles=(),
        expansion_rows=1,
        proof_premise_rows=0,
        conflict_rows=0,
        token_count_kind="estimated",
        token_count=4,
        serialized_utf8_bytes=0,
    ).measured(RecallBudget())
    assert pack.to_json() == json.dumps(json.loads(pack.to_json()), sort_keys=True, separators=(",", ":"))
    assert EvidencePack.from_json(pack.to_json()) == pack
    with pytest.raises(InvalidInput):
        EvidencePack.from_json({**json.loads(pack.to_json()), "path": "/tmp"})
    with pytest.raises(InvalidInput):
        replace(pack, status="contradicted")
