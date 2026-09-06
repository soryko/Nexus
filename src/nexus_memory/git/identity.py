from __future__ import annotations

from pathlib import Path

from nexus_memory.domain.errors import RepositoryRegistrationFailed
from nexus_memory.domain.models import RepositoryBinding, Scope
from nexus_memory.storage.repository import MemoryRepository

from .cli import GitCli, GitCliVerifier
from .token import TOKEN_DIRECTORY, TOKEN_FILE, publish_token, read_token, token_path

__all__ = ["TOKEN_DIRECTORY", "TOKEN_FILE", "bind_repository", "locate_without_git",
           "publish_token", "read_token", "token_path"]


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

    The verifier is handed the locator and the token this binding was made against, so it
    can confirm before every later verification that the path still hosts this repository.
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
    return binding, GitCliVerifier(git, checkout, binding.object_format, info.common_dir, token)
