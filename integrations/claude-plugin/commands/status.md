---
description: Print whether this project is enrolled in Nexus and whether the memory hooks are active. Read-only; never enrols.
allowed-tools: Bash(python3 -I:*)
---

Enrolment, from the installed resolver:

!`python3 -I "${CLAUDE_PLUGIN_ROOT}/hooks/status.py"`

Report exactly one status line, then stop. Do not call any Nexus tool and do not suggest enrolling from inside this session.

- If the enrolment line says ENROLLED and your context contains the sentence "Nexus project memory is available through the nexus_memory MCP tools", reply: `Nexus: enrolled, hooks active.`
- If it says ENROLLED and that sentence is absent from your context, reply: `Nexus: enrolled, hooks not trusted or not running (the SessionStart cue did not arrive).`
- If it says NOT ENROLLED or UNKNOWN, reply: `Nexus: not enrolled here. Enrolment is an owner command, run outside Claude Code.`
