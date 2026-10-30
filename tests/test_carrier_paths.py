"""No build machine's folders or user ship in a prebuilt engine: scripts/build-carrier.sh drops the CUDA engines' debug
sections as it copies them (their code, data and kernels stay byte for byte) and refuses a payload that still names this
machine's home or user. The DGX Spark's H3 and Drama 3 builds take their engines from the 4.2.1 carrier, and the Mac's
Bolt from the 4.2.1 release's archive, whose binary lost its symbol table's build paths the same way."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_the_carrier_strips_every_cuda_engine_it_copies_and_refuses_a_leak():
    script = (ROOT / "scripts/build-carrier.sh").read_text()
    assert "nodebug() {" in script and "strip --strip-debug" in script
    for copied in ("payload/spark-engine/libtf_h3.so", "payload/spark-music3/music3-generate",
                   "payload/spark-drama3/drama3-generate payload/spark-drama3/libtensorfold_drama3.so"):
        assert f"nodebug {copied}" in script, copied
    # each engine is stripped before its checksums are written, so the sums are the shipped bytes'
    for strip, sums in (("nodebug payload/spark-engine/", "cd payload/spark-engine && sha256sum"),
                        ("nodebug payload/spark-music3/", "cd payload/spark-music3 && sha256sum"),
                        ("nodebug payload/spark-drama3/", "cd payload/spark-drama3 && sha256sum")):
        assert script.index(strip) < script.index(sums), strip
    scan = script.index('grep -rlaF -e "$HOME/" -e "/$(id -un)/" payload')
    assert scan < script.index("docker buildx build") and scan < script.index("docker build -f")


def test_the_spark_h3_and_drama3_builds_and_the_mac_bolt_use_the_4_2_1_parts():
    for name in ("spark/build-engine.sh", "spark/build-drama3.sh"):
        assert 'IMAGE="${IMAGE:-ghcr.io/drowzeys/keys-tensorfold-studio:4.2.1}"' in (ROOT / name).read_text(), name
    bolt = (ROOT / "scripts/setup-bolt.sh").read_text()
    url = re.search(r'^METAL_URL="([^"]+)"', bolt, re.M).group(1)
    assert url.endswith("/releases/download/v4.2.1/bolt-metal-772da34b-macos-arm64.tar.gz")
    assert re.search(r'^METAL_SHA256="04e73bf49246180bb13a7088415e559ff1288a983f0ce6b3f8643aaaaa9b6a01"', bolt, re.M)
    assert (ROOT / "VERSION").read_text().strip() == "4.3.1"  # the app; its engines stay the 4.2.1 carrier's


@pytest.mark.skipif(not shutil.which("cc") or not shutil.which("strip"), reason="needs a C compiler and binutils")
def test_dropping_debug_sections_removes_the_build_folder_and_keeps_the_code(tmp_path):
    folder = tmp_path / "home" / "builder" / "src"
    folder.mkdir(parents=True)
    (folder / "engine.c").write_text('int answer(void) { return 42; }\nint main(void) { return answer() - 42; }\n')
    built = folder / "engine"
    subprocess.run(["cc", "-g", "-O1", "-o", str(built), str(folder / "engine.c")], check=True, cwd=folder)
    assert str(folder).encode() in built.read_bytes()
    stripped = tmp_path / "engine.stripped"
    subprocess.run(["strip", "--strip-debug", "-o", str(stripped), str(built)], check=True)
    assert str(folder).encode() not in stripped.read_bytes()
    assert subprocess.run([str(stripped)]).returncode == 0
