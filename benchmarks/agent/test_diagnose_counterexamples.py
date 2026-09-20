"""Counterexamples for `diagnose_heldout`'s write detection. Run directly; no model, no fixtures.

Every case below is one the parser once got WRONG. They are kept as tests rather than as a
changelog entry because the failure mode is silent: a missed write is reported as "this run
never mutated source", which reads exactly like a run that explored and gave up.

The parser is not a shell parser and must never pretend to be. The second block asserts the
cases it is REQUIRED to give up on -- those must land in `unknown`, never in "no write".

    python3 test_diagnose_counterexamples.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import diagnose_heldout as D                                            # noqa: E402

SRC = "src/click/core.py"

# (command, expected targets, expected unknown-or-more) -- each was a real defect.
MUST_DETECT = [
    # `&>` redirects BOTH streams into a file. The fd-duplication stripper ate it, so this
    # classified as no write at all.
    ("printf changed &> " + SRC,                                   [SRC]),
    ("printf changed &>> " + SRC,                                  [SRC]),
    # An interpreter writing through pathlib, which no `open(...,'w')` pattern matches.
    (f"""python -c "from pathlib import Path; Path('{SRC}').write_text('changed')" """, [SRC]),
    (f"""python3 -c "open('{SRC}','w').write('x')" """,             [SRC]),
    # BSD `sed -i` takes a mandatory suffix argument; the old pattern captured the SCRIPT and
    # reported `s/a/b/` as the file being written.
    (f"sed -i '' s/a/b/ {SRC}",                                     [SRC]),
    (f"sed -i s/a/b/ {SRC}",                                        [SRC]),
    (f"cat > {SRC} <<EOF",                                          [SRC]),
    (f"tee {SRC} < /dev/null",                                      [SRC]),
]

# Read-only commands. A false positive here costs an `unknown` that cries wolf, which makes
# the unknown column worthless.
MUST_IGNORE = [
    "pytest tests/test_basic.py -q 2>&1 | tail -20",
    "echo hi > /dev/null",
    "python3 -c \"import click; print(click.__version__)\"",
    "grep -rn 'call_on_close' src/ 2>/dev/null | head",
    "ls -la 2>&1; git log --oneline -5",
]

# Cases the parser CANNOT resolve and must report as unknown rather than as absent.
MUST_BE_UNKNOWN = [
    'f=$(mktemp); printf x > "$f"',                  # target is a variable
    'eval "printf x > $TARGET"',                     # target built at runtime
    'python3 -c "import sys; open(sys.argv[1],\'w\')" out.txt',   # path not a literal
    'for f in src/click/*.py; do printf x > "$f"; done',          # glob in a loop
]


def main() -> int:
    bad = []
    for cmd, expect in MUST_DETECT:
        found, outside, unknown = D._bash_targets(cmd)
        if not set(expect).issubset(set(found)):
            bad.append(f"MISSED  {cmd[:70]!r}\n        got targets={found} unknown={unknown}")

    for cmd in MUST_IGNORE:
        found, outside, unknown = D._bash_targets(cmd)
        if found or unknown or outside:
            bad.append(f"FALSE+  {cmd[:70]!r}\n        got targets={found} unknown={unknown}")

    for cmd in MUST_BE_UNKNOWN:
        found, outside, unknown = D._bash_targets(cmd)
        if not (unknown or found or outside):
            bad.append(f"SILENT  {cmd[:70]!r}\n        resolved to nothing at all; must be unknown")

    total = len(MUST_DETECT) + len(MUST_IGNORE) + len(MUST_BE_UNKNOWN)
    for b in bad:
        print(b)
    print(f"{total - len(bad)}/{total} counterexamples pass")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
