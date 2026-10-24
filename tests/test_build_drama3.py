"""spark/build-drama3.sh's decisions (no GPU, no Docker, no compiler: stand-ins on PATH) and spark/drama3_codec.py's
pinned fetch: the GB10 gate, an install already at the commit, the carrier's commit and checksums deciding, and the
fall back to a source build when the carrier's engine is not the pinned one."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "spark" / "build-drama3.sh"
COMMIT = "81173e6c612739a3427844fd8131cf4905e0a9c4"


def stub(folder: Path, name: str, body: str) -> None:
    path = folder / name
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)


@pytest.fixture
def box(tmp_path):
    """A PATH of stand-ins: a GB10, an aarch64 machine, Docker whose image holds ``payload`` (or nothing), no nvcc."""

    bin_dir, payload, prefix = tmp_path / "bin", tmp_path / "payload", tmp_path / "prefix"
    bin_dir.mkdir()
    payload.mkdir()
    stub(bin_dir, "nvidia-smi", 'case "$*" in *compute_cap*) echo "${FAKE_CC:-12.1}";; *) echo "${FAKE_GPU:-NVIDIA GB10}";; esac\n')
    stub(bin_dir, "uname", '[ "$1" = -m ] && { echo aarch64; exit 0; }; exec /usr/bin/uname "$@"\n')
    # docker run --rm -v DIR:/out IMAGE sh -c '...': the image's /payload/spark-drama3 copied into DIR
    stub(bin_dir, "docker", f'''echo "$*" >> "{tmp_path}/docker.log"
case "$1" in
  info|pull) exit 0;;
  image) exit 0;;
  run) shift; while [ "$1" != -v ]; do shift; done; out="${{2%%:/out}}"; cp "{payload}"/* "$out"/ 2>/dev/null; exit 0;;
esac
''')
    real = shutil.which("sha256sum")  # /usr/bin on Linux, /sbin on a recent Mac (shasum -a 256 on an older one)
    stub(bin_dir, "sha256sum", f'exec "{real}" "$@"\n' if real else 'exec shasum -a 256 "$@"\n')
    # never a real build here, whatever this machine has: no compiler, and git and curl that fetch nothing
    stub(bin_dir, "git", "exit 1\n")
    stub(bin_dir, "curl", "exit 1\n")
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin", "PREFIX": str(prefix), "HOME": str(tmp_path),
           "NVCC": str(tmp_path / "no-nvcc")}
    env.pop("ENGINE_FROM_SOURCE", None)
    env.pop("DRAMA3_SOURCE", None)
    return {"tmp": tmp_path, "payload": payload, "prefix": prefix, "env": env}


def run(box, **extra) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT)], env={**box["env"], **extra}, capture_output=True, text=True, timeout=60)


def carrier(box, commit: str = COMMIT, tamper: bool = False) -> None:
    payload = box["payload"]
    (payload / "drama3-generate").write_bytes(b"\x7fELF engine")
    (payload / "libtensorfold_drama3.so").write_bytes(b"\x7fELF library")
    (payload / "commit").write_text(commit + "\n")
    lines = [f"{hashlib.sha256((payload / n).read_bytes()).hexdigest()}  {n}" for n in ("drama3-generate", "libtensorfold_drama3.so", "commit")]
    (payload / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    if tamper:
        (payload / "drama3-generate").write_bytes(b"\x7fELF something else")


def test_it_refuses_another_gpu(box):
    done = run(box, FAKE_GPU="NVIDIA H100 80GB HBM3")
    assert done.returncode == 1 and "measured on a GB10" in done.stderr and "DRAMA3_ANY_GPU=1" in done.stderr
    assert not (box["prefix"] / "drama3").exists()


def test_an_sm_120_card_builds_for_its_own_target(box):
    """An RTX Pro 6000 Blackwell (compute capability 12.0) is not refused: it goes on to a source build for sm_120
    (here it stops at the missing nvcc)."""

    done = run(box, FAKE_GPU="NVIDIA RTX PRO 6000 Blackwell Workstation Edition", FAKE_CC="12.0")
    assert "untested there" not in done.stderr and "DRAMA3_ANY_GPU=1" not in done.stderr and "no nvcc" in done.stderr


def test_the_build_target_follows_the_gpu(box):
    """sm_121 on a GB10, sm_120 on an RTX Pro 6000, TF_SM over both, and 121 when the driver answers with words."""

    script = SCRIPT.read_text()
    line = next(one for one in script.splitlines() if one.startswith('SM="${TF_SM:-'))
    guard = next(one for one in script.splitlines() if one.startswith('case "$SM" in'))
    probe = box["tmp"] / "sm.sh"
    probe.write_text(line + "\n" + guard + '\necho "$SM"\n')
    said = lambda **extra: subprocess.run(["bash", str(probe)], env={**box["env"], **extra}, capture_output=True, text=True).stdout.strip()
    assert said() == "121" and said(FAKE_CC="12.0") == "120" and said(TF_SM="120,121") == "120,121"
    assert said(FAKE_CC='Field "compute_cap" is not a valid field to query.') == "121"
    assert '-Dsm="$SM"' in script and "-Dsm=121" not in script
    echo = next(one for one in script.splitlines() if "building the drama3 engine" in one)
    assert "sm_${SM//,/, sm_}" in echo and "sm_121" not in echo


def test_the_carrier_engine_is_taken_when_its_commit_and_checksums_match(box):
    carrier(box)
    done = run(box)
    assert done.returncode == 0, done.stderr
    out = box["prefix"] / "drama3"
    assert "checksums verified" in done.stdout
    assert (out / "bin/drama3-generate").read_bytes() == b"\x7fELF engine" and os.access(out / "bin/drama3-generate", os.X_OK)
    assert (out / "lib/libtensorfold_drama3.so").read_bytes() == b"\x7fELF library"
    assert (out / "commit").read_text().strip() == COMMIT and "drama3-cuda 81173e6c (prebuilt" in (out / "BUILD").read_text()
    assert not (box["prefix"] / "drama3.fetch").exists()
    # a second run finds it installed and asks Docker nothing
    log = box["tmp"] / "docker.log"
    log.unlink()
    again = run(box)
    assert again.returncode == 0 and "already built at 81173e6c" in again.stdout and not log.exists()


@pytest.mark.parametrize("bad", ["tampered", "another commit"])
def test_a_carrier_engine_that_is_not_the_pinned_one_is_not_used(box, bad):
    carrier(box, commit="0" * 40 if bad == "another commit" else COMMIT, tamper=bad == "tampered")
    done = run(box)  # then the source build, which needs nvcc: none here
    assert done.returncode == 1 and "holds no drama3 engine at 81173e6c" in done.stdout and "no nvcc" in done.stderr
    assert not (box["prefix"] / "drama3/bin/drama3-generate").exists()
    assert not (box["prefix"] / "drama3.fetch").exists()


def test_without_docker_or_with_engine_from_source_it_builds(box):
    carrier(box)
    done = run(box, ENGINE_FROM_SOURCE="1")
    assert done.returncode == 1 and "no nvcc" in done.stderr and not (box["tmp"] / "docker.log").exists()


def test_an_engine_built_by_hand_at_the_commit_is_kept(box):
    out = box["prefix"] / "drama3"
    (out / "bin").mkdir(parents=True)
    (out / "lib").mkdir()
    (out / "bin/drama3-generate").write_bytes(b"\x7fELF")
    (out / "bin/drama3-generate").chmod(0o755)
    (out / "lib/libtensorfold_drama3.so").write_bytes(b"\x7fELF")
    (out / "BUILD").write_text("TensorFold drama3-cuda 81173e6c (built 2026-10-15 15:38:26 from ~/zig-port/tf-drama3)\n")
    done = run(box)
    assert done.returncode == 0 and "already built" in done.stdout
    assert (out / "commit").read_text().strip() == COMMIT
    # one at another commit is not
    (out / "commit").unlink()
    (out / "BUILD").write_text("TensorFold drama3-cuda 12345678\n")
    assert "already built" not in run(box).stdout


# ---- spark/drama3_codec.py ----

def codec_module():
    spec = importlib.util.spec_from_file_location("drama3_codec_under_test", ROOT / "spark" / "drama3_codec.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Reply:
    def __init__(self, data: bytes):
        self.data = data

    def read(self, _n=-1):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_the_codec_converter_fetches_only_the_files_it_was_checked_with(tmp_path, monkeypatch):
    one = codec_module()
    good, bad = b"print('reference')\n", b"print('changed upstream')\n"
    monkeypatch.setattr(one, "SOURCES", {"fishcodec/rvq.py": ("https://example/rvq.py", hashlib.sha256(good).hexdigest())})
    asked = []
    one.fetch(tmp_path, opener=lambda url: asked.append(url) or Reply(good))
    assert (tmp_path / "fishcodec/rvq.py").read_bytes() == good and asked == ["https://example/rvq.py"]
    one.fetch(tmp_path, opener=lambda url: pytest.fail("a file already there and matching is not fetched again"))
    (tmp_path / "fishcodec/rvq.py").write_bytes(bad)
    with pytest.raises(SystemExit, match="not the file this converter was checked with"):
        one.fetch(tmp_path, opener=lambda url: Reply(bad))
    # every pinned source names a commit, never a branch
    for name, (url, digest) in codec_module().SOURCES.items():
        assert len(digest) == 64 and ("/main/" not in url and "/master/" not in url), name


def test_a_finished_conversion_is_known_by_its_header(tmp_path, monkeypatch):
    one = codec_module()
    monkeypatch.setattr(one, "MIN_BYTES", 16)
    path = tmp_path / "codec-v1.safetensors"

    def write(meta: dict) -> None:
        head = json.dumps({"__metadata__": meta}).encode()
        path.write_bytes(struct.pack("<Q", len(head)) + head + b"\0" * 64)

    write({"format": "drama3-codec-v1"})
    assert one.converted(path)
    write({"format": "something else"})
    assert not one.converted(path)
    path.write_bytes(b"not a safetensors file at all, long enough")
    assert not one.converted(path)
    assert not one.converted(tmp_path / "missing.safetensors")
    assert one.main([str(tmp_path / "no-checkpoint"), str(path)]) == 1
