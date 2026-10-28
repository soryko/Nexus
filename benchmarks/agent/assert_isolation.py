"""Assert the isolation properties, rather than print booleans and let a reader judge.

The verification report used to print `cross_arm_read_blocked: True` inside a fenced block and
move on; nothing compared it to anything, so a `False` would have been published just as
calmly. This exits nonzero when any required property does not hold.

Usage:  assert_isolation.py <arm-dir>
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

BENCH = Path(__file__).parent
sys.path.insert(0, str(BENCH))

import a1_config                                                        # noqa: E402
import isolation                                                        # noqa: E402

REQUIRED = {
    "heredoc_works": True,            # the repair
    "other_sandbox_executes": True,   # the positive control: the sibling really ran
    "cross_arm_read_blocked": True,   # the property the repair must not have broken
    "tmpprefix_set": True,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: assert_isolation.py <arm-dir>", file=sys.stderr)
        return 2
    base = Path(argv[1])
    t = Path(tempfile.mkdtemp())
    a, b = t / "armA", t / "armB"
    for arm in (a, b):
        (arm / "tmp").mkdir(parents=True)
        shutil.copytree(base / "repo", arm / "repo", symlinks=True)

    def profile(me: Path, other: Path) -> Path:
        return isolation.write_profile(
            me / "sandbox.sb", me / "repo", [other],
            allow_paths=isolation.default_allow_paths(me / "repo") + [me],
            forwarder_port=8899)

    pa, pb = profile(a, b), profile(b, a)
    env = a1_config.child_env("http://127.0.0.1:8899", os.environ.get("DEEPSEEK_API_KEY", ""),
                              {"TMPPREFIX": str(a / "tmp" / "zsh")})
    r = isolation.heredoc_probe(pa, a / "repo", env, pb, b / "repo",
                                other_prefix=str(b / "tmp" / "zsh"))

    bad = []
    for key, want in REQUIRED.items():
        got = r.get(key)
        print(f"  {key}: {got}" + ("" if got == want else f"   <-- REQUIRED {want}"))
        if got != want:
            bad.append(f"{key}={got!r} (required {want!r})")
    for k, v in r.items():
        if k not in REQUIRED:
            print(f"  {k}: {v}")
    if bad:
        print("\nISOLATION NOT DEMONSTRATED: " + "; ".join(bad))
        return 1
    print("\nisolation demonstrated: heredocs work, the sibling sandbox runs, "
          "and it cannot read this arm's heredoc scratch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
