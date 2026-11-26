"""Contract for the graph's trusted snapshot side (handoff §§3, 6, 7)."""
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from typing import get_type_hints

import pytest

from nexus_memory.domain import graph
from nexus_memory.domain.errors import InvalidInput


REF = graph.AssertionRevisionRef('claim', 'rev')
STAMP = '2026-01-01T00:00:00Z'


def test_assertion_lookup_returns_recorded_revision():
    assert get_type_hints(graph.GraphReadSnapshot.assertion)['return'] != graph.AssertionDraft | None
    qualifiers = {'paths': ['a']}
    row = graph.AssertionRecord(
        ref=REF, subject=graph.EntityRef('a'), predicate='CALLS', object=graph.EntityRef('b'),
        qualifiers=qualifiers, origin_kind='parser', review_state='accepted', producer_version='v1',
        validity=graph.Validity.unbounded(), applicability=graph.Applicability(),
        recorded_at=STAMP, graph_event_seq=17,
    )
    assert get_type_hints(graph.GraphReadSnapshot.assertion)['return'] == type(row) | None
    assert (row.ref, row.recorded_at, row.graph_event_seq) == (REF, STAMP, 17)
    qualifiers['paths'].append('b')
    assert row.qualifiers['paths'] == ('a',)
    with pytest.raises(FrozenInstanceError):
        row.graph_event_seq = 19
    with pytest.raises(InvalidInput):
        replace(row, recorded_at='2026-01-01')
    with pytest.raises(InvalidInput):
        replace(row, graph_event_seq=True)


def test_evidence_lookup_preserves_exact_capture_and_origin():
    assert get_type_hints(graph.GraphReadSnapshot.evidence)['return'] != tuple[graph.EvidenceInput, ...]
    data = 'e\u0301'.encode()
    span = graph.Span(8, 8 + len(data), sha256(data).hexdigest())
    row = graph.EvidenceRecord(evidence_id='ev', target=REF, role='support',
                               source=graph.CodeSource('repo', 'f' * 40, 'src/a', 'b' * 40),
                               span=span, captured_bytes=data, origin_identity='origin')
    assert get_type_hints(graph.GraphReadSnapshot.evidence)['return'] == tuple[type(row), ...]
    assert (row.target, row.span, row.captured_bytes, row.origin_identity) == (REF, span, data, 'origin')
    with pytest.raises(FrozenInstanceError):
        row.captured_bytes = b''
    for invalid in (data[:-1], b'xyz', bytearray(data)):
        with pytest.raises(InvalidInput):
            replace(row, captured_bytes=invalid)
    with pytest.raises(InvalidInput):
        replace(row, captured_bytes=b'\xff', span=graph.Span(0, 1, sha256(b'\xff').hexdigest()))


@pytest.mark.parametrize('value', ['2026-01-01', '20260101T000000Z', '2026-01-01 00:00:00Z',
                                   '2026-01-01T00:00:00', '2026-01-01T00:00:00+00:00',
                                   '2026-01-01T00:00:00-01:00', '2026-02-30T00:00:00Z'])
def test_interval_requires_rfc3339_z(value):
    with pytest.raises(InvalidInput):
        graph.Validity.from_json({'kind': 'interval', 'valid_from': value, 'valid_to': '2027' + value[4:]})


def test_interval_compares_full_fractional_seconds():
    a = '2026-01-01T00:00:00.0000001Z'
    b = '2026-01-01T00:00:00.0000002Z'
    assert graph.Validity('interval', a, b).valid_to == b
    for start, end in ((b, a), (a, a[:-1] + '0Z')):
        with pytest.raises(InvalidInput):
            graph.Validity('interval', start, end)
