"""The checkout token: publication, reading and validation.

Its own module because two callers need it and neither may import the other. Binding
(``identity``) publishes and reads it; the verifier (``cli``) re-reads it before every new
verification to confirm the path it is about to run git in still hosts the repository this
process bound.
"""
from __future__ import annotations

import os
import re
import secrets
import tempfile
from pathlib import Path

from nexus_memory.domain.errors import RepositoryRegistrationFailed

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
