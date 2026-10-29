"""Every engine build fetches Zig through a check of the SHA-256 ziglang.org publishes: spark/zig.sh on Linux, the
embedded sum in scripts/build-music3-metal.sh on a Mac. A tampered archive is refused and deleted, never unpacked."""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LINUX_BUILDS = ["spark/build-bolt.sh", "spark/build-drama3.sh", "spark/build-engine.sh", "spark/build-music3.sh"]


def test_no_build_script_unpacks_zig_without_its_checksum():
    for name in LINUX_BUILDS:
        script = (ROOT / name).read_text()
        assert "ziglang.org/download" not in script, name
        assert '/zig.sh"' in script and 'ZIG="$(studio_zig "$PREFIX")"' in script, name
    metal = (ROOT / "scripts/build-music3-metal.sh").read_text()
    assert re.search(r'^ZIG_SHA256="[0-9a-f]{64}"', metal, re.M), "the Mac's Zig sum is empty"


def test_the_linux_sums_are_the_same_everywhere():
    helper = (ROOT / "spark/zig.sh").read_text()
    yue2 = (ROOT / "spark/build-yue2-engine.sh").read_text()
    for arch in ("aarch64", "x86_64"):
        ours = re.search(rf"STUDIO_ZIG_SHA256_{arch}=([0-9a-f]{{64}})", helper).group(1)
        assert f"ZIG_SHA256_{arch}={ours}" in yue2


@pytest.mark.skipif(sys.platform == "darwin", reason="spark/zig.sh is the Linux builds' helper")
def test_a_tampered_zig_archive_is_refused_and_deleted(tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    # a curl that "downloads" something that is not Zig
    (fake / "curl").write_text('#!/bin/bash\nwhile [ $# -gt 0 ]; do [ "$1" = -o ] && { echo not-zig > "$2"; shift; }; shift; done\n')
    (fake / "curl").chmod(0o755)
    prefix = tmp_path / "prefix"
    done = subprocess.run(["bash", "-c", f'. "{ROOT}/spark/zig.sh"; studio_zig "{prefix}"'], capture_output=True, text=True,
                          env={**os.environ, "PATH": f"{fake}:/usr/bin:/bin"})
    assert done.returncode != 0
    assert "does not match its published SHA-256" in done.stderr
    assert not list(prefix.glob("*.part")) and not (prefix / "zig-0.17.0" / "zig").exists()
