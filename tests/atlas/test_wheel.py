"""The built wheel must carry every file the viewer serves; a source checkout hides a missing one."""
from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

from nexus_memory.atlas import server

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "src" / "nexus_memory" / "atlas" / "static"


def test_the_wheel_contains_every_static_file_the_server_can_serve(tmp_path) -> None:
    uv = shutil.which("uv")
    assert uv, "uv is required to build the wheel"
    subprocess.run([uv, "build", "--wheel", "--out-dir", str(tmp_path), str(ROOT)], check=True, capture_output=True)
    (wheel,) = tmp_path.glob("nexus_memory-*.whl")
    with zipfile.ZipFile(wheel) as archive:
        packaged = {n: archive.read(n) for n in archive.namelist() if n.startswith("nexus_memory/atlas/static/")}
    served = {name for name, _ in server._STATIC.values()}
    on_disk = {p.name for p in STATIC.iterdir() if p.is_file()}
    assert served == on_disk, "the allowlist and the static directory disagree"
    assert {Path(n).name for n in packaged} == on_disk
    for name in on_disk:
        assert packaged[f"nexus_memory/atlas/static/{name}"] == (STATIC / name).read_bytes(), name
