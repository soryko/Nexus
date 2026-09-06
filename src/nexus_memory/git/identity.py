from __future__ import annotations

import os
from pathlib import Path

from nexus_memory.domain.errors import (
    RepositoryMismatch,
    RepositoryRegistrationFailed,
    RepositoryUnbound,
    VerificationUnavailable,
)
from nexus_memory.domain.models import OBJECT_FORMATS, RepositoryBinding, Scope
from nexus_memory.storage.repository import MemoryRepository

from .cli import GitCli, GitCliVerifier
from .token import TOKEN_DIRECTORY, TOKEN_FILE, publish_token, read_token, token_path

__all__ = [
    "TOKEN_DIRECTORY", "TOKEN_FILE", "bind_repository", "locate_without_git",
    "publish_token", "read_token", "token_path",
]


# git validates HEAD through a 256-byte buffer and decides on what fits; so do we.
_HEAD_BYTES = 256
# git's own hex table takes either case. Membership is tested on raw bytes, never on text:
# decoding first, with anything but `strict`, can drop a byte and shorten a value into a
# match it should not have made.
_HEX_BYTES = frozenset(b"0123456789abcdefABCDEF")


def _valid_head(head: Path) -> bool:
    """Whether ``HEAD`` holds a form git accepts, not merely that something is there.

    git will not call a directory a repository until HEAD parses as a symbolic ref into
    ``refs/`` — a file, or historically a symlink — or as a detached object id. Testing only
    for existence left the same disagreement the empty ``.git`` had: a HEAD holding
    arbitrary text is rejected by git with exit 128 while discovery accepted it. An unborn
    branch stays valid, because ``ref: refs/heads/main`` is well-formed before that ref
    exists; that case is exactly why identity is not derived from history.
    """
    try:
        if head.is_symlink():
            return os.readlink(head).startswith("refs/")
        with head.open("rb") as handle:
            content = handle.read(_HEAD_BYTES)
    except OSError:
        return False
    if content.startswith(b"ref:"):
        return content[len(b"ref:"):].lstrip().startswith(b"refs/")
    # Detached: git reads an object id from the head of the buffer and disregards whatever
    # follows, so its permitted trailing data is preserved by testing only the prefix. The
    # id must nonetheless be present in full — a value shorter than a width is not a short
    # object id, it is not an object id — and every byte of that prefix must be hex.
    return any(len(content) >= width and all(byte in _HEX_BYTES for byte in content[:width])
               for width in OBJECT_FORMATS.values())


def _holds_repository_metadata(git_dir: Path) -> bool:
    """Whether a directory carries the metadata git requires before calling it a repository.

    That a ``.git`` exists proves nothing: a freshly created empty one makes git exit 128,
    and accepting it here reports the checkout as discoverable — which is the one thing
    that licenses :func:`bind_repository` to suppress git's own rejection. git wants an
    object store, a ref store and a HEAD it can parse, so those are what is asked for here.

    This narrows the disagreement with git; it does not end it, and the claim should not be
    made that it does. git also reads ``core.repositoryformatversion`` and refuses a
    repository whose extensions it does not know, and it tests the two directories for
    access rather than for being directories. A launch is refused where git refuses for a
    reason visible here, and where it refuses for one that is not, the degraded path can
    still bind a checkout git would reject.
    """
    return (git_dir / "objects").is_dir() and (git_dir / "refs").is_dir() and _valid_head(git_dir / "HEAD")


def _git_directory_at(directory: Path) -> Path | None:
    """The shared Git directory for a ``.git`` entry in one directory, or ``None``.

    Called only where a ``.git`` entry exists, because the first one found decides: an
    unusable ``.git`` stops discovery rather than letting it walk on into an ancestor
    repository, which is what git does and is the only safe answer when the question is
    which identity a path belongs to.
    """
    dot_git = directory / ".git"
    try:
        if dot_git.is_dir():
            git_dir = dot_git
        elif dot_git.is_file():
            line = dot_git.read_text("utf-8").strip()
            if not line.startswith("gitdir:"):
                return None
            git_dir = (directory / line[len("gitdir:"):].strip()).resolve()
        else:
            return None
        common = git_dir / "commondir"
        if common.is_file():
            git_dir = (git_dir / common.read_text("utf-8").strip()).resolve()
        git_dir = git_dir.resolve()
        return git_dir if _holds_repository_metadata(git_dir) else None
    except OSError:
        return None


def locate_without_git(checkout: Path) -> Path | None:
    """Discover the shared Git directory the way git does, without running it.

    Handles a ``.git`` directory, a ``.git`` file (``gitdir: …``) as linked worktrees use,
    and that directory's ``commondir`` file — and, like git, walks up through the parents
    until one of them has a ``.git``, stopping at a filesystem boundary. The walk is not a
    nicety: ``--repo repo/sub`` is a supported way to name a checkout when git is present,
    so a degraded launch that looked only at ``sub/.git`` would report a different binding
    for the same argument. Registration is not attempted this way: the object format needs
    git.
    """
    try:
        if not checkout.is_dir():
            return None
        current = checkout.resolve()
        device = current.stat().st_dev
    except OSError:
        return None
    for directory in (current, *current.parents):
        try:
            if directory.stat().st_dev != device:
                return None  # git does not cross a filesystem boundary while discovering
            present = (directory / ".git").exists()
        except OSError:
            return None
        if present:
            return _git_directory_at(directory)
    return None


def bind_repository(store: MemoryRepository, scope: Scope, checkout: Path, repository_id: str | None = None,
                    git: GitCli | None = None) -> tuple[RepositoryBinding | None, GitCliVerifier | None]:
    """Bind the launch checkout: token first, then one immediate transaction.

    Publication precedes the row on purpose. An interruption between them leaves a
    published token with no row, which the next launch adopts; the reverse order could
    leave a row for a token that was never written. Without git an already-registered
    checkout still binds, read-only; nothing is registered and no verifier is returned.
    """
    if git is not None:
        try:
            info = git.inspect(checkout)
        except (RepositoryUnbound, VerificationUnavailable):
            # An executable that cannot answer is not evidence about the checkout: a git
            # that is present but broken must degrade exactly like a missing one, or a
            # single unusable binary takes the whole server down. It degrades only where
            # the checkout is discoverable without git; where it is not, the path really
            # may not be a checkout, and the operator hears the original error.
            if locate_without_git(checkout) is None:
                raise
        else:
            token = read_token(info.common_dir)
            if token is None:
                token = publish_token(info.common_dir)
            binding = store.bind_checkout(scope, token, str(info.common_dir), info.object_format, repository_id)
            return binding, GitCliVerifier(git, checkout, binding.object_format, info.common_dir, token)
    return _bind_read_only(store, scope, checkout, repository_id)


def _bind_read_only(store: MemoryRepository, scope: Scope, checkout: Path,
                    repository_id: str | None) -> tuple[RepositoryBinding | None, None]:
    """Bind an already-registered checkout without git. Nothing is registered here.

    ``--repo-id`` still means what it means with git present. Ignoring it because git is
    unavailable would let a launch that names a conflicting identity look successfully
    bound, and the operator would learn otherwise only from what later evidence claims;
    an unusable token is still an error rather than an absent one, for the same reason.
    """
    common_dir = locate_without_git(checkout)
    token = read_token(common_dir) if common_dir is not None else None
    binding = store.checkout_binding(scope, token) if token is not None else None
    if binding is None:
        if repository_id is not None:
            raise RepositoryRegistrationFailed("git is unavailable, so the checkout cannot be registered")
        return None, None
    if repository_id is not None and repository_id != binding.repository_id:
        raise RepositoryMismatch("checkout is registered to a different repository")
    return binding, None
