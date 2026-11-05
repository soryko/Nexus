"""Read-only enrolment check shared by the plugin's command hooks.

It asks the installed project-scope resolver whether `cwd` is an enrolled checkout. It never
enrols anything and never opens a store. Anything it cannot determine is "unknown", and the
callers treat unknown like not enrolled: say nothing about memory.
"""
import os
import sys
from pathlib import Path

DEFAULT_BIN = Path.home() / ".local/share/nexus-memory/project-scopes-v1/bin"


def enrollment(cwd):
    """Return "enrolled", "not-enrolled" or "unknown"."""
    bin_dir = Path(os.environ.get("NEXUS_SCOPE_BIN") or DEFAULT_BIN)
    if not (bin_dir / "project_scope.py").is_file() or not cwd or not os.path.isabs(cwd):
        return "unknown"
    sys.path.insert(0, str(bin_dir))
    try:
        import project_scope
        try:
            project_scope.resolve(Path(cwd))
        except project_scope.ScopeError:
            return "not-enrolled"
        return "enrolled"
    except Exception:
        return "unknown"
    finally:
        sys.path.remove(str(bin_dir))
