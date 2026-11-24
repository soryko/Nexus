#!/usr/bin/env python3
"""Print-only status for /nexus-memory:status. Reads; never enrols, writes or starts a server."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scope import capture_policy, enrollment  # noqa: E402

LINES = {
    "enrolled": "enrolment: ENROLLED (this checkout is an owner-enrolled Nexus project)",
    "not-enrolled": "enrolment: NOT ENROLLED (memory hooks are off here; enrolling is an owner command, run outside this session)",
    "unknown": "enrolment: UNKNOWN (the project-scope resolver is not installed or could not be read; treated as not enrolled)",
}


def main():
    print(LINES[enrollment(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())])
    print(f"policy: {capture_policy()} (set NEXUS_CAPTURE_POLICY=explicit for ask-first capture; restart Claude Code after changing it)")


if __name__ == "__main__":
    main()
