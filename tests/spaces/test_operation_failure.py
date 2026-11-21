import pytest

from nexus_memory.domain import errors
from nexus_memory.domain.spaces import OperationRef
from nexus_memory.transport import mcp_server


@pytest.mark.parametrize(('code', 'retryable'), [
    ('authority_busy', True), ('publication_busy', True),
    ('publication_unknown', False), ('grant_revoked', False),
])
def test_scoped_mcp_failure(code, retryable):
    result = mcp_server._error(code, 'SELECT private_body')
    assert result.is_error
    assert result.structured_content == {'code': code, 'retryable': retryable}
    assert 'private_body' not in result.model_dump_json()


def test_reconcile_reference_is_only_in_owner_view():
    ref = OperationRef('private-stream', 'private-key')
    failure = errors.OperationFailure('publication_unknown', False, ref)
    public = {'code': 'publication_unknown', 'retryable': False}
    assert failure.for_owner() == {**public, 'reconciliation_ref': {
        'writer_stream_id': 'private-stream', 'operation_key': 'private-key',
    }}
    assert failure.for_model() == public
    result = mcp_server._operation_error(failure)
    assert result.structured_content == public
    assert 'private-' not in result.model_dump_json()
    assert errors.OperationFailure('not_found', False).for_owner() == {
        'code': 'not_found', 'retryable': False,
    }


@pytest.mark.parametrize(('code', 'retryable'), [('private-code', False), ('authority_busy', 1)])
def test_reject_unknown_codes_and_non_boolean_retryable(code, retryable):
    with pytest.raises(errors.InvalidInput):
        errors.OperationFailure(code, retryable)


def test_legacy_errors_remain_text_only():
    result = mcp_server._error('not_found', 'memory not found')
    assert result.is_error and result.structured_content is None
    assert result.content[0].text == 'not_found: memory not found'
