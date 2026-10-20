"""FastH3 with a style LoRA on a Mac (scripts/video.sh, zig_fasth3.py): the LoRA goes to the native engine when its
h3_case.py takes --lora, and a clip the native engine refuses (status 3) renders on MLX. The engine and Python are
fakes that record how they were called. python3 -m pytest tests/test_native_lora.py"""

import os
import platform
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    platform.system() != "Darwin"
    or "Apple M5" not in subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout,
    reason="scripts/video.sh picks the native engine on an M5 only")

FAKE_PYTHON = """#!/bin/bash
echo "$*" >> "$CALLS"
case "$1" in *zig_fasth3.py) exit "${ZIG_STATUS:-0}";; esac
exit 0
"""


def run(tmp_path, engine_takes_lora: bool, zig_status: int = 0, lora: str = "/loras/style.mlx.safetensors:0.8"):
    prefix = tmp_path / "prefix"
    (prefix / "venv/bin").mkdir(parents=True)
    python = prefix / "venv/bin/python"
    python.write_text(FAKE_PYTHON)
    python.chmod(0o755)
    (prefix / "zig_fasth3.py").write_text("")
    (prefix / "h3_generate.py").write_text("")
    engine = prefix / "zig-engine"
    engine.mkdir()
    (engine / "tf-h3-dit").write_text("")
    (engine / "tf-h3-dit").chmod(0o755)
    (engine / "h3_case.py").write_text('parser.add_argument("--lora")' if engine_takes_lora else "")
    fasth3 = tmp_path / "FastH3"
    fasth3.mkdir()
    (fasth3 / "fastvideo_inference.json").write_text("{}")
    calls = tmp_path / "calls.txt"
    env = {**os.environ, "PREFIX": str(prefix), "H3_MODEL_DIR": str(tmp_path / "H3"), "FASTH3_DIR": str(fasth3),
           "ENGINE": "fasth3", "LORA": lora + "\n", "CALLS": str(calls), "ZIG_STATUS": str(zig_status)}
    done = subprocess.run(["bash", str(ROOT / "scripts/video.sh"), "a prompt", str(tmp_path / "out.mp4")], env=env,
                          capture_output=True, text=True)
    return done, calls.read_text().splitlines() if calls.exists() else []


def test_a_lora_stays_on_the_native_engine_that_takes_it(tmp_path):
    done, calls = run(tmp_path, engine_takes_lora=True)
    assert done.returncode == 0, done.stderr
    assert len(calls) == 1 and "zig_fasth3.py" in calls[0]
    assert "--lora /loras/style.mlx.safetensors:0.8" in calls[0]


def test_an_older_native_engine_leaves_the_lora_to_mlx(tmp_path):
    done, calls = run(tmp_path, engine_takes_lora=False)
    assert done.returncode == 0, done.stderr
    assert "engine: mlx (a LoRA is merged by the MLX engine (this native engine is older))" in done.stdout
    assert len(calls) == 1 and "h3_generate.py" in calls[0] and "--lora /loras/style.mlx.safetensors:0.8" in calls[0]


def test_an_adapter_the_native_engine_refuses_renders_on_mlx(tmp_path):
    done, calls = run(tmp_path, engine_takes_lora=True, zig_status=3)
    assert done.returncode == 0, done.stderr
    assert "engine: mlx (the native engine cannot apply this LoRA)" in done.stdout
    assert [("zig_fasth3.py" in c, "h3_generate.py" in c) for c in calls] == [(True, False), (False, True)]


def test_a_native_failure_is_not_retried_on_mlx(tmp_path):
    done, calls = run(tmp_path, engine_takes_lora=True, zig_status=1)
    assert done.returncode == 1 and len(calls) == 1
