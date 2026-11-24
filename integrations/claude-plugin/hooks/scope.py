"""Read-only enrolment check shared by the plugin's command hooks.

It asks the installed project-scope resolver whether `cwd` is an enrolled checkout. It never
enrols anything and never opens a store. Anything it cannot determine is "unknown", and the
callers treat unknown like not enrolled: say nothing about memory.
"""
import os
import sys
from contextlib import contextmanager
from pathlib import Path


def capture_policy():
    """"autonomous" unless NEXUS_CAPTURE_POLICY=explicit; the launcher applies the same rule.

    An unrecognised value is treated as explicit, the more conservative mode.
    """
    value = os.environ.get("NEXUS_CAPTURE_POLICY", "").strip().lower()
    return "autonomous" if value in ("", "autonomous") else "explicit"


@contextmanager
def _resolver(cwd):
    """Yield the installed project_scope module, or None when it or an absolute cwd is missing."""
    # NEXUS_SCOPE_BIN / NEXUS_SCOPE_HOME are test knobs for disposable profiles; unset in normal use.
    if os.environ.get("NEXUS_SCOPE_HOME"):
        os.environ["HOME"] = os.environ["NEXUS_SCOPE_HOME"]
    bin_dir = Path(os.environ.get("NEXUS_SCOPE_BIN") or Path("~/.local/share/nexus-memory/project-scopes-v1/bin").expanduser())
    if not (bin_dir / "project_scope.py").is_file() or not cwd or not os.path.isabs(cwd):
        yield None
        return
    sys.path.insert(0, str(bin_dir))
    try:
        import project_scope
        yield project_scope
    finally:
        sys.path.remove(str(bin_dir))


def project(cwd):
    """The resolved enrolled project for `cwd`, or None when not enrolled or undeterminable."""
    try:
        with _resolver(cwd) as project_scope:
            return project_scope.resolve(Path(cwd)) if project_scope is not None else None
    except Exception:
        return None


def enrollment(cwd):
    """Return "enrolled", "not-enrolled" or "unknown"."""
    try:
        with _resolver(cwd) as project_scope:
            if project_scope is None:
                return "unknown"
            try:
                project_scope.resolve(Path(cwd))
            except project_scope.ScopeError:
                return "not-enrolled"
            return "enrolled"
    except Exception:
        return "unknown"
