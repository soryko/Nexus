#!/usr/bin/env python3
"""Delegate to the privately installed Nexus project-scope integration."""
import os
from pathlib import Path
import sys

base = Path.home() / '.local/share/nexus-memory'
entry = base / 'project-scopes-v1/bin/antigravity_hook.py'
os.execv(sys.executable, [sys.executable, '-I', str(entry), 'start', *sys.argv[1:]])
