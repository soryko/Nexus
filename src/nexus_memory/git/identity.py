from __future__ import annotations

import os
import re
import secrets
import tempfile
from pathlib import Path

from nexus_memory.domain.errors import RepositoryRegistrationFailed
from nexus_memory.domain.models import RepositoryBinding, Scope
from nexus_memory.storage.repository import MemoryRepository

from .cli import GitCli, GitCliVerifier

TOKEN_DIRECTORY = "nexus"
TOKEN_FILE = "checkout-token"
_TOKEN = re.compile(r"[0-9a-f]{64}\n?\Z")


def token_path(common_dir: Path) -> Path:
    return common_dir / TOKEN_DIRECTORY / TOKEN_FILE


def read_token(common_dir: Path) -> str | None:
    """The registered token, ``None`` when absent, or a clear error when it is unusable.

    An invalid or truncated token is never treated as absent: doing so would mint a fresh
    identity behind the operator's back. The file is left in place; removing it is the
    operator's decision, and the message names no path.
    """
    try:
        data = token_path(common_dir).read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise RepositoryRegistrationFailed("the checkout token could not be read") from error
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as error:
        raise RepositoryRegistrationFailed(
            "the checkout token is invalid and was left in place; remove it to register again"
        ) from error
    if not _TOKEN.fullmatch(text):
        raise RepositoryRegistrationFailed(
            "the checkout token is invalid or truncated and was left in place; remove it to register again"
        )
    return text[:64]


def publish_token(common_dir: Path) -> str:
    """Publish one complete token without overwriting a winner.

    The token is written whole to a private temporary file, flushed, and then hard-linked
    to its final name. ``link(2)`` refuses an existing target, so of two processes that
    publish at once the first wins and the second reads the winner. A token at the final
    path is therefore always complete, whatever was interrupted.
    """
    directory = common_dir / TOKEN_DIRECTORY
    final = token_path(common_dir)
    try:
        directory.mkdir(exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=TOKEN_FILE + ".", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write((secrets.token_hex(32) + "\n").encode("ascii"))
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, final)
            except FileExistsError:
                pass  # a winner is already published; read it below
            _fsync_directory(directory)
        finally:
            os.unlink(temporary)
    except OSError as error:
        raise RepositoryRegistrationFailed("the checkout token could not be written") from error
    token = read_token(common_dir)
    if token is None:
        raise RepositoryRegistrationFailed("the checkout token could not be written")
    return token


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def locate_without_git(checkout: Path) -> Path | None:
    """Follow ``.git`` the way git does, so a registered checkout still binds when git is absent.

    Handles a ``.git`` directory, a ``.git`` file (``gitdir: …``) as linked worktrees use,
    and that directory's ``commondir`` file. Registration is not attempted this way: the
    object format needs git.
    """
    dot_git = checkout / ".git"
    try:
        if dot_git.is_dir():
            git_dir = dot_git
        elif dot_git.is_file():
            line = dot_git.read_text("utf-8").strip()
            if not line.startswith("gitdir:"):
                return None
            git_dir = (checkout / line[len("gitdir:"):].strip()).resolve()
        else:
            return None
        common = git_dir / "commondir"
        if common.is_file():
            git_dir = (git_dir / common.read_text("utf-8").strip()).resolve()
        return git_dir.resolve()
    except OSError:
        return None


def bind_repository(store: MemoryRepository, scope: Scope, checkout: Path, repository_id: str | None = None,
                    git: GitCli | None = None) -> tuple[RepositoryBinding | None, GitCliVerifier | None]:
    """Bind the launch checkout: token first, then one immediate transaction.

    Publication precedes the row on purpose. An interruption between them leaves a
    published token with no row, which the next launch adopts; the reverse order could
    leave a row for a token that was never written. Without git an already-registered
    checkout still binds, read-only; nothing is registered and no verifier is returned.
    """
    if git is None:
        common_dir = locate_without_git(checkout)
        if common_dir is None:
            return None, None
        token = read_token(common_dir)
        binding = store.checkout_binding(scope, token) if token is not None else None
        if binding is None and repository_id is not None:
            raise RepositoryRegistrationFailed("git is unavailable, so the checkout cannot be registered")
        return binding, None
    info = git.inspect(checkout)
    token = read_token(info.common_dir)
    if token is None:
        token = publish_token(info.common_dir)
    binding = store.bind_checkout(scope, token, str(info.common_dir), info.object_format, repository_id)
    return binding, GitCliVerifier(git, checkout, binding.object_format)
