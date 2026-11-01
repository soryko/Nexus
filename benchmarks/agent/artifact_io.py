"""Read and publish grading evidence without following links in arm-writable directories."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import secrets
import stat
import sys
import tempfile

MAX_TEXT_BYTES = 64 * 1024 * 1024


@contextmanager
def directory_fd(path: Path, *, parents: bool = False):
    """Walk every component from the filesystem root through held directory descriptors."""
    path = Path(path)
    if ".." in path.parts:
        raise OSError(f"parent traversal refused: {path}")
    path = path.absolute()
    if sys.platform == "darwin" and path.parts[1:2] in (("tmp",), ("var",)):
        # These are system-owned macOS aliases. No arm-controlled component is resolved.
        path = Path("/private", *path.parts[1:])
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    current = os.open("/", flags)
    try:
        for name in path.parts[1:]:
            if parents:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=current)
                except FileExistsError:
                    pass
            child = os.open(name, flags, dir_fd=current)
            os.close(current)
            current = child
        yield current
    finally:
        os.close(current)


def _regular(info: os.stat_result, name: str) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise OSError(f"special or multiply linked file refused: {name}")


@contextmanager
def regular_at(directory: int, name: str):
    # lstat rejects devices before open. fstat also checks what we actually opened in
    # case the arm replaced the entry. O_NONBLOCK prevents a FIFO from hanging the harness.
    _regular(os.stat(name, dir_fd=directory, follow_symlinks=False), name)
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
    with os.fdopen(os.open(name, flags, dir_fd=directory), "rb") as stream:
        _regular(os.fstat(stream.fileno()), name)
        yield stream


@contextmanager
def open_regular(path: Path):
    with directory_fd(path.parent) as directory, regular_at(directory, path.name) as stream:
        yield stream


def read_text(path: Path) -> str:
    with open_regular(path) as stream:
        data = stream.read(MAX_TEXT_BYTES + 1)
    if len(data) > MAX_TEXT_BYTES:
        raise OSError(f"grading artifact exceeds {MAX_TEXT_BYTES} bytes: {path}")
    return data.decode("utf-8")


def _publish(path: Path, text: str, *, exclusive: bool, parents: bool):
    with directory_fd(path.parent, parents=parents) as fd:
        if not exclusive:
            try:
                existing = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                _regular(existing, path.name)
        name = f".nexus-{secrets.token_hex(16)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        file_fd = os.open(name, flags, 0o600, dir_fd=fd)
        try:
            with os.fdopen(file_fd, "w", encoding="utf-8") as out:
                out.write(text)
                out.flush()
                os.fsync(out.fileno())
            if exclusive:
                # Same-directory link publishes the complete evidence or fails with EEXIST.
                os.link(name, path.name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
            else:
                os.replace(name, path.name, src_dir_fd=fd, dst_dir_fd=fd)
        finally:
            try:
                os.unlink(name, dir_fd=fd)
            except FileNotFoundError:
                pass


def create_text(path: Path, text: str, *, parents: bool = False) -> None:
    _publish(path, text, exclusive=True, parents=parents)


def write_text(path: Path, text: str, *, parents: bool = False) -> None:
    _publish(path, text, exclusive=False, parents=parents)


def require_staging_filesystem(directory: int) -> None:
    if os.stat(tempfile.gettempdir()).st_dev != os.fstat(directory).st_dev:
        raise OSError("set TMPDIR to a private directory on the sweep scratch filesystem "
                      "before starting; repository and store staging require an atomic rename")


@contextmanager
def staged_directory(destination: Path):
    """Claim a fresh repo/store path, then publish its prepared directory without following links."""
    with directory_fd(destination.parent, parents=True) as parent:
        require_staging_filesystem(parent)
        with tempfile.TemporaryDirectory(prefix="nexus-prepare-") as td:
            # Claim first, including against a symlink or an interrupted earlier preparation.
            # A failed preparation leaves the empty claim in place for inspection.
            os.mkdir(destination.name, mode=0o700, dir_fd=parent)
            stage = Path(td) / "prepared"
            yield stage
            # Only an empty directory can be replaced. Never copy through a destination.
            os.rename(stage, destination.name, dst_dir_fd=parent)
