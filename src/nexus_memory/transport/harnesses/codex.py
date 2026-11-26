"""Launch-bound checkout checks shared with explicit-only Codex clients."""
from pathlib import Path
from nexus_memory.domain.errors import RepositoryMismatch
from nexus_memory.git.identity import locate_without_git


def checkout_root(cwd: Path) -> Path:
    current = cwd.resolve(strict=True)
    if not current.is_dir():
        raise RepositoryMismatch("Codex cwd is not a checkout directory")
    for directory in (current, *current.parents):
        if (directory / ".git").exists():
            if locate_without_git(directory) is not None:
                return directory
            break
    raise RepositoryMismatch("Codex cwd is not a valid checkout")


def resolve_checkout(project_root: Path, cwd: Path) -> Path:
    """Allow linked worktrees, never an unrelated checkout or a fresh clone."""
    allowed = checkout_root(project_root)
    active = checkout_root(cwd)
    if locate_without_git(allowed) != locate_without_git(active):
        raise RepositoryMismatch("Codex cwd belongs to a different repository")
    return active
