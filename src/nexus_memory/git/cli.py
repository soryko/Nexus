from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from nexus_memory.domain.errors import (
    CommitNotFound,
    InvalidReference,
    RepositoryUnbound,
    VerificationUnavailable,
)
from nexus_memory.domain.models import HEX, OBJECT_FORMATS, TreeEntry

# Environment variables that would redirect git away from the checkout named at launch.
# Removing them keeps the binding a property of the path, not of whoever launched us.
_REDIRECTING = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
)


@dataclass(frozen=True, slots=True)
class CheckoutInfo:
    common_dir: Path        # canonical: --path-format=absolute, then realpath
    object_format: str      # "sha1" or "sha256"


class GitCli:
    """The only code that spawns ``git`` or reads its output.

    Every invocation carries ``--no-replace-objects --no-lazy-fetch --literal-pathspecs``
    and runs with ``GIT_TERMINAL_PROMPT=0``. These are guarantees, not hygiene: a
    replacement ref substitutes a tree silently, a partial clone fetches on read, and
    pathspec magic turns caller text into a pattern. stderr is captured and never
    surfaced, because git's messages disclose the checkout location.
    """

    CONTROLS = ("--no-replace-objects", "--no-lazy-fetch", "--literal-pathspecs")

    def __init__(self, executable: str = "git", timeout: float = 30.0) -> None:
        self.executable = executable
        self.timeout = timeout

    @staticmethod
    def available(executable: str = "git") -> bool:
        return shutil.which(executable) is not None

    def _environment(self) -> dict[str, str]:
        environment = {key: value for key, value in os.environ.items() if key not in _REDIRECTING}
        environment["GIT_TERMINAL_PROMPT"] = "0"
        return environment

    def _run(self, cwd: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        command = [self.executable, *self.CONTROLS, *arguments]
        try:
            return subprocess.run(
                command, cwd=str(cwd), env=self._environment(), capture_output=True,
                timeout=self.timeout, check=False, stdin=subprocess.DEVNULL,
            )
        except (FileNotFoundError, PermissionError, NotADirectoryError) as error:
            raise VerificationUnavailable("git is unavailable") from error
        except subprocess.TimeoutExpired as error:
            raise VerificationUnavailable("git did not answer in time") from error

    def inspect(self, checkout: Path) -> CheckoutInfo:
        """Locate the shared Git directory and read the object format, for binding."""
        if not checkout.is_dir():
            raise RepositoryUnbound("the repository path is not a directory")
        result = self._run(checkout, "rev-parse", "--path-format=absolute", "--git-common-dir", "--show-object-format")
        if result.returncode != 0:
            raise RepositoryUnbound("the repository path is not a git checkout")
        lines = result.stdout.decode("utf-8", "surrogateescape").splitlines()
        if len(lines) != 2 or lines[1] not in OBJECT_FORMATS:
            raise RepositoryUnbound("the repository could not be inspected")
        return CheckoutInfo(Path(lines[0]).resolve(), lines[1])


class GitCliVerifier:
    """Resolve a commit spec once; read one tree entry per path against the resolved commit.

    Acceptance of an entry (one record, byte-equal name, blob, supported mode) is decided
    by the service, not here: this class returns what git returned.
    """

    def __init__(self, git: GitCli, checkout: Path, object_format: str) -> None:
        self.git = git
        self.checkout = checkout
        self.width = OBJECT_FORMATS[object_format]

    def resolve_commit(self, spec: str) -> str | None:
        """Typed resolution: an annotated tag dereferences to its commit, a tree is refused."""
        result = self.git._run(self.checkout, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{spec}^{{commit}}")
        if result.returncode != 0:
            return None
        oid = result.stdout.decode("ascii", "replace").strip()
        if not HEX.fullmatch(oid):
            raise CommitNotFound("commit could not be resolved")
        return oid

    def tree_entries(self, commit_oid: str, path: str) -> tuple[TreeEntry, ...]:
        """Raw ``ls-tree --full-tree -z`` records for a pathspec against a fixed commit.

        Absence is empty output with exit 0, never a non-zero exit; a non-zero exit after a
        successful resolution is a pathspec git would not take, which validation should
        already have refused.
        """
        result = self.git._run(self.checkout, "ls-tree", "--full-tree", "-z", commit_oid, "--", path)
        if result.returncode != 0:
            raise InvalidReference("reference could not be checked against the commit")
        entries = []
        for record in result.stdout.split(b"\0"):
            if not record:
                continue
            try:
                header, name = record.split(b"\t", 1)
                mode, entry_type, oid = header.decode("ascii").split(" ")
            except (ValueError, UnicodeDecodeError) as error:
                raise InvalidReference("reference could not be checked against the commit") from error
            entries.append(TreeEntry(mode, entry_type, oid, name.decode("utf-8", "surrogateescape")))
        return tuple(entries)
