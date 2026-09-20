#!/usr/bin/env python3
"""Compatibility entry point. The checker itself now lives in the package.

It moved because verifying an installation should not require the source checkout that
built it: `nexus-memory-check` is installed beside `nexus-memory`, and works when this
file is gone. This wrapper stays so that the command published with v0.1.0a1 keeps
working, and it delegates rather than duplicating -- two copies of a checker are two
checkers that can disagree, and the one people run is the one that would be wrong.

Deliberately NO `sys.path` manipulation. Adding the checkout's `src/` here would make this
script test the working tree instead of the installation, and would keep it "passing" on a
host where the package is not actually installed. So run it with the interpreter of the
environment under test:

    /path/to/venv/bin/python tools/check_install.py --server /path/to/venv/bin/nexus-memory

or, preferably, just use the installed command:

    /path/to/venv/bin/nexus-memory-check
"""
from nexus_memory.install_check import main

if __name__ == "__main__":
    raise SystemExit(main())
