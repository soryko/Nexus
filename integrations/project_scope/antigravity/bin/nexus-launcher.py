#!/usr/bin/env python3
"""Delegate to the privately installed Nexus project-scope integration."""
import os
from pathlib import Path
import sys

base = Path.home() / '.local/share/nexus-memory'
entry = base / 'project-scopes-v1/bin/launcher.py'
os.execv(sys.executable, [sys.executable, '-I', str(entry), 'antigravity', str(base / 'venvs/0.1.0a2/bin/nexus-memory'), *sys.argv[1:]])
