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
