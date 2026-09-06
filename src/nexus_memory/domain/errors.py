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
