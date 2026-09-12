"""Kernel-enforced isolation for one arm, and probes that run inside the boundary.

Two surfaces, both enforced by `sandbox-exec` rather than requested by environment:

  network     everything denied except localhost, which reaches only `model_forwarder.py`
              and therefore only the model endpoint. DNS is denied too, so a name lookup
              for an external host fails before a socket is opened.
  filesystem  the held-out acceptance checks, the sibling arms' checkouts, the scoring
              work areas and the source clone are denied to the arm.

The probes execute INSIDE the profile the arm will use, because a probe run outside it
measures the harness's own reachability and not the arm's. The previous egress probe also
ran with PIP_NO_INDEX set, so it could fail without attempting a connection at all; these
deliberately leave the index enabled.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROFILE = """(version 1)
(allow default)

; ---- network: deny everything, then re-admit only the local forwarder ----
(deny network-outbound)
(allow network-outbound (remote ip "localhost:*"))

; ---- filesystem: the arm may not read the answers or its siblings ----
{denies}
"""


def profile_for(arm_repo: Path, deny_paths: list[Path]) -> str:
    denies = "\n".join(f'(deny file-read* (subpath "{p}"))' for p in deny_paths)
    return PROFILE.format(denies=denies)


def write_profile(path: Path, arm_repo: Path, deny_paths: list[Path]) -> Path:
    path.write_text(profile_for(arm_repo, [p.resolve() for p in deny_paths if p.exists()]))
    return path


def probe(profile: Path, argv: list[str], cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["sandbox-exec", "-f", str(profile), *argv], cwd=str(cwd),
                          capture_output=True, text=True, timeout=timeout)


def paired(profile: Path, argv: list[str], cwd: Path) -> dict:
    """Run an action inside the boundary AND outside it.

    A negative control that fails for its own reasons proves nothing -- the first filesystem
    probe "passed" with `No such file or directory`, which is what an absent path says too.
    A boundary is demonstrated only when the same action succeeds without it and fails with
    it, so both halves are run and both are recorded.
    """
    inside = subprocess.run(["sandbox-exec", "-f", str(profile), *argv], cwd=str(cwd),
                            capture_output=True, text=True, timeout=90)
    outside = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=90)
    return {"blocked_inside": inside.returncode != 0,
            "works_outside": outside.returncode == 0,
            "demonstrates_boundary": inside.returncode != 0 and outside.returncode == 0,
            "inside_tail": (inside.stderr or inside.stdout).strip().splitlines()[-1:],
            "outside_tail": (outside.stderr or outside.stdout).strip().splitlines()[-1:]}


def check_boundary(profile: Path, cwd: Path, held_checks: Path, python: str) -> dict:
    """Negative controls, executed inside the boundary. All must FAIL for the arm to run."""
    out: dict = {}

    # network: index deliberately ENABLED, so a failure means the socket was refused
    dl = probe(profile, [python, "-m", "pip", "download", "click", "--no-deps",
                         "-d", str(cwd / ".probe-dl")], cwd)
    fetched = sorted(p.name for p in (cwd / ".probe-dl").glob("*")) if (cwd / ".probe-dl").exists() else []
    out["network_egress_blocked"] = dl.returncode != 0 and not fetched
    out["network_probe_tail"] = (dl.stderr or dl.stdout).strip().splitlines()[-1:]

    # DNS specifically: a name lookup for an external host must not succeed
    dns = probe(profile, ["/usr/bin/curl", "-sS", "-m", "6", "-o", "/dev/null",
                          "https://pypi.org/simple/click/"], cwd)
    out["dns_and_https_blocked"] = dns.returncode != 0
    out["dns_probe_tail"] = (dns.stderr or "").strip().splitlines()[-1:]

    # filesystem: the held-out checks must be unreadable from inside
    target = held_checks
    fs = probe(profile, ["/bin/sh", "-c", f'cat "{target}"/tests/*.py'], cwd)
    out["held_checks_unreadable"] = fs.returncode != 0
    out["fs_probe_tail"] = (fs.stderr or "").strip().splitlines()[-1:]

    # the filesystem probe is only meaningful against its unsandboxed twin
    held_file = next(iter(held_checks.rglob("*.py")), None)
    if held_file is not None:
        out["held_checks_paired"] = paired(profile, ["/bin/cat", str(held_file)], cwd)
        out["held_checks_unreadable"] = out["held_checks_paired"]["demonstrates_boundary"]
    else:
        out["held_checks_paired"] = {"demonstrates_boundary": False,
                                     "reason": "no held check file found -- probe would be vacuous"}
        out["held_checks_unreadable"] = False

    out["all_hold"] = all(out[k] for k in
                          ("network_egress_blocked", "dns_and_https_blocked",
                           "held_checks_unreadable"))
    return out


if __name__ == "__main__":
    repo, held, py = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    prof = write_profile(Path("/tmp/a1-probe.sb"), repo, [held, *held.parent.parent.glob("arms")])
    import json
    print(json.dumps(check_boundary(prof, repo, held, py), indent=1))
