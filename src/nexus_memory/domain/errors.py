from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .spaces import OperationRef


class NexusError(Exception):
    code = "nexus_error"


class InvalidInput(NexusError):
    code = "invalid_input"


class MemoryNotFound(NexusError):
    code = "not_found"


class RevisionConflict(NexusError):
    code = "revision_conflict"


class IdempotencyConflict(NexusError):
    code = "idempotency_conflict"


class UnsupportedSchema(NexusError):
    code = "unsupported_schema"


class UnsupportedRuntime(NexusError):
    code = "unsupported_runtime"


class StorageIntegrityError(NexusError):
    code = "storage_integrity"


class InvalidQuery(NexusError):
    code = "invalid_query"


class CursorExpired(NexusError):
    code = "cursor_expired"


# B2a. Every one of these is a direct subclass: the transport recognises safe error text
# by NexusError's direct subclasses, and none of them may carry a path, git text or SQL.


class RepositoryUnbound(NexusError):
    code = "repository_unbound"


class RepositoryMismatch(NexusError):
    code = "repository_mismatch"


class RepositoryRegistrationFailed(NexusError):
    code = "repository_registration_failed"


class VerificationUnavailable(NexusError):
    code = "verification_unavailable"


class CommitNotFound(NexusError):
    code = "commit_not_found"


class PathNotInCommit(NexusError):
    code = "path_not_in_commit"


class UnsupportedReferenceType(NexusError):
    code = "unsupported_reference_type"


class InvalidReference(NexusError):
    code = "invalid_reference"


class SpaceNotMounted(NexusError):
    code = "space_not_mounted"


class SpaceReadOnly(NexusError):
    code = "space_read_only"


class GrantRevoked(NexusError):
    code = "grant_revoked"


class AuthorityBusy(NexusError):
    code = "authority_busy"


class PublicationBusy(NexusError):
    code = "publication_busy"


class PublicationUnknown(NexusError):
    code = "publication_unknown"


class FlowDenied(NexusError):
    code = "flow_denied"


class AmbiguousEntity(NexusError):
    code = "ambiguous_entity"


class InvalidRelation(NexusError):
    code = "invalid_relation"


class InvalidEvidence(NexusError):
    code = "invalid_evidence"


class UnsupportedCapability(NexusError):
    code = "unsupported_capability"


class StalePack(NexusError):
    code = "stale_pack"


class ResolutionConflict(NexusError):
    code = "resolution_conflict"


class EventIdentityConflict(NexusError):
    code = "event_identity_conflict"


class EvidenceTooLarge(NexusError):
    code = "evidence_too_large"


class InvalidGraphPattern(NexusError):
    code = "invalid_graph_pattern"


class InvalidRetrievalBudget(NexusError):
    code = "invalid_retrieval_budget"


# Only fixed messages cross the transport for broker-profile errors.
SCOPED_ERROR_MESSAGES = {
    SpaceNotMounted.code: "space not mounted",
    SpaceReadOnly.code: "space read only",
    GrantRevoked.code: "grant revoked",
    AuthorityBusy.code: "authority busy",
    PublicationBusy.code: "publication busy",
    PublicationUnknown.code: "publication unknown",
    FlowDenied.code: "flow denied",
    AmbiguousEntity.code: "ambiguous entity",
    InvalidRelation.code: "invalid relation",
    InvalidEvidence.code: "invalid evidence",
    UnsupportedCapability.code: "unsupported capability",
    StalePack.code: "stale pack",
    ResolutionConflict.code: "resolution conflict",
    EventIdentityConflict.code: "event identity conflict",
    EvidenceTooLarge.code: "evidence too large",
    InvalidGraphPattern.code: "invalid graph pattern",
    InvalidRetrievalBudget.code: "invalid retrieval budget",
}

_OPERATION_CODES = frozenset(SCOPED_ERROR_MESSAGES) | {
    InvalidInput.code, MemoryNotFound.code, RevisionConflict.code, IdempotencyConflict.code,
    UnsupportedSchema.code, StorageIntegrityError.code, 'internal_error',
}


@dataclass(frozen=True, slots=True)
class OperationFailure:
    """A broker failure, not a receipt or a statement about write outcome."""

    code: str
    retryable: bool
    reconciliation_ref: OperationRef | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if type(self.code) is not str or self.code not in _OPERATION_CODES:
            raise InvalidInput('invalid operation failure code')
        if type(self.retryable) is not bool:
            raise InvalidInput('invalid retryable flag')
        if self.reconciliation_ref is not None:
            from .spaces import OperationRef

            if not isinstance(self.reconciliation_ref, OperationRef):
                raise InvalidInput('invalid reconciliation reference')

    def for_model(self) -> dict[str, object]:
        return {'code': self.code, 'retryable': self.retryable}

    def for_owner(self) -> dict[str, object]:
        """For trusted code that has already authorized the owner."""
        payload = self.for_model()
        if (ref := self.reconciliation_ref) is not None:
            payload['reconciliation_ref'] = {
                'writer_stream_id': ref.writer_stream_id, 'operation_key': ref.operation_key,
            }
        return payload
