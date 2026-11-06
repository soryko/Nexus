"""Lexical conventions shared by storage profiles and automatic recall."""
import re


_SEPARATORS = ("_", "/", "\\", ".")
_CAMEL = re.compile(r"[a-z0-9][A-Z]")
_ALNUM_MIX = re.compile(r"(?:[A-Za-z][0-9])|(?:[0-9][A-Za-z])")


def is_code_shaped(chunk: str) -> bool:
    """Whether a whitespace-delimited chunk looks like an identifier, path or symbol.

    The test is deliberately syntactic and local. A chunk qualifies
    if a separator sits between alphanumerics (``transcode_worker.py``, ``/v3/assets``),
    if it is camelCase, or if letters and digits are adjacent (``mux2``, ``h264``).
    Trailing sentence punctuation is stripped first so that an ordinary word ending a
    sentence is not mistaken for a dotted name.
    """
    chunk = chunk.strip("`'\"(),;:!?“”‘’")
    chunk = chunk.rstrip(".")
    if not chunk or not any(character.isalnum() for character in chunk):
        return False
    for separator in _SEPARATORS:
        position = chunk.find(separator)
        while position != -1:
            before = chunk[position - 1] if position > 0 else ""
            after = chunk[position + 1] if position + 1 < len(chunk) else ""
            if before.isalnum() and after.isalnum():
                return True
            position = chunk.find(separator, position + 1)
    return bool(_CAMEL.search(chunk) or _ALNUM_MIX.search(chunk))
