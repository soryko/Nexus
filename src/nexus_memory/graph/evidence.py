"""Materialize immutable code evidence outside the graph writer transaction."""
from dataclasses import dataclass
from hashlib import sha256
from nexus_memory.domain.errors import InvalidEvidence
from nexus_memory.domain.graph import CodeSource, Span
from nexus_memory.domain.models import HEX, validate_reference_path


@dataclass(frozen=True)
class CapturedEvidence:
    source: CodeSource
    span: Span
    captured_bytes: bytes


def capture_git_span(binding, commit_oid, path, span):
    identity, verifier = binding
    validate_reference_path(path)
    if len(commit_oid) != verifier.width or not HEX.fullmatch(commit_oid):
        raise InvalidEvidence('exact commit required')
    verifier.check_identity()
    if verifier.resolve_commit(commit_oid) != commit_oid:
        raise InvalidEvidence('commit unavailable')
    entries = verifier.tree_entries(commit_oid,path)
    if (len(entries) != 1 or entries[0].path != path or entries[0].entry_type != 'blob'
            or entries[0].mode not in ('100644','100755')):
        raise InvalidEvidence('not an exact regular file')
    source = CodeSource(identity.repository_id,commit_oid,path,entries[0].object_oid)
    blob = verifier.git.read_blob(source.blob_oid,max_bytes=1<<20,checkout=verifier.checkout)
    data = blob[span.start:span.end]
    if span.end > len(blob) or sha256(data).hexdigest() != span.sha256:
        raise InvalidEvidence('span mismatch')
    try:
        data.decode('utf-8')
    except UnicodeDecodeError:
        raise InvalidEvidence('invalid UTF-8 span') from None
    verifier.check_identity()
    return CapturedEvidence(source,span,data)
