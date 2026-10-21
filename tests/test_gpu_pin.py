"""spark/gpu.sh pins a process to the GPU the operator names.

Run: python3 -m pytest tests/test_gpu_pin.py
"""
import os
import subprocess
from pathlib import Path

import suite_gpu

ROOT = Path(__file__).resolve().parent.parent
GPU_SH = ROOT / "spark" / "gpu.sh"


def pin(tmp_path, rows, **env):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "nvidia-smi"
    stub.write_text("#!/bin/bash\nprintf '%s\\n' " + " ".join("'" + row + "'" for row in rows) + "\n")
    stub.chmod(0o755)
    script = (
        "set -e\n"
        f'. "{GPU_SH}"\n'
        "studio_pin_gpu\n"
        'printf "gpu=%s cuda=%s\\n" "${STUDIO_GPU-}" "${CUDA_VISIBLE_DEVICES-}"\n'
    )
    clean = {key: value for key, value in os.environ.items() if key not in ("STUDIO_GPU", "CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER")}
    return subprocess.run(["bash", "-c", script], cwd=tmp_path, env={**clean, "PATH": f"{bin_dir}:/usr/bin:/bin", **env},
                          capture_output=True, text=True, timeout=10)


def test_two_compute_gpus_require_an_index(tmp_path):
    done = pin(tmp_path, [
        "0, 16311, Enabled",
        "1, 97887, Disabled",
        "2, 97887, Disabled",
    ])
    assert done.returncode == 1
    assert "STUDIO_GPU" in done.stderr
    assert "gpu=" not in done.stdout


def test_one_compute_card_behind_a_display_is_used(tmp_path):
    done = pin(tmp_path, ["0, 16311, Enabled", "2, 97887, Disabled"])
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "gpu=2 cuda=2"


def test_studio_gpu_is_the_index_the_operator_passes(tmp_path):
    done = pin(tmp_path, ["0, 16311, Enabled", "1, 97887, Disabled", "2, 97887, Disabled"], STUDIO_GPU="2")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "gpu=2 cuda=2"


def test_the_rtx_entry_points_the_spark_installer_at_the_named_card(tmp_path):
    """oneshot-setup-rtx-6000.sh does not copy the installer. Its nvidia-smi answers queries for STUDIO_GPU."""

    rtx = (ROOT / "oneshot-setup-rtx-6000.sh").read_text()
    spark = (ROOT / "oneshot-setup-spark.sh").read_text()
    assert 'exec bash "$HERE/oneshot-setup-cuda.sh"' in rtx
    assert 'exec bash "$HERE/oneshot-setup-cuda.sh"' in spark
    assert "studio_pin_gpu" in rtx and "studio_pin_gpu" not in spark
    cuda = (ROOT / "oneshot-setup-cuda.sh").read_text()
    assert "COMFY_COMMIT=" not in spark and "COMFY_COMMIT=" in cuda
    assert 'PATH="$HERE/spark/smi-pin:$PATH"' in cuda
    stub = tmp_path / "nvidia-smi"
    stub.write_text("#!/bin/bash\nprintf '%s\\n' \"$*\"\n")
    stub.chmod(0o755)
    pin = ROOT / "spark" / "smi-pin"
    env = {k: v for k, v in os.environ.items() if k != "NVIDIA_SMI_BIN"}
    env.update(STUDIO_GPU="2", NVIDIA_SMI_BIN=str(stub), PATH=f"{pin}:/usr/bin:/bin")
    query = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], env=env, capture_output=True, text=True)
    listing = subprocess.run(["nvidia-smi", "-L"], env=env, capture_output=True, text=True)
    assert query.stdout.strip() == "-i 2 --query-gpu=name --format=csv,noheader"
    assert listing.stdout.strip() == "-L"


def test_one_gpu_stays_on_the_default_device(tmp_path):
    done = pin(tmp_path, ["0, [N/A], Disabled"])
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "gpu= cuda="


def test_the_suite_reads_the_first_compute_gpu_and_leaves_a_spark_alone():
    """pytest is one process. Two compute cards do not make it stop and ask, and a GB10 is not pinned."""

    rows = "0, 16311, Enabled\n1, 97887, Disabled\n2, 97887, Disabled\n"
    assert suite_gpu.compute_indexes(rows) == ["1", "2"]
    assert suite_gpu.choose_suite_gpu(["1", "2"]) == "1"
    assert suite_gpu.choose_suite_gpu(["1", "2"], "2") == "2"
    assert suite_gpu.choose_suite_gpu(["1", "2"], "", "2") == "2"
    assert suite_gpu.choose_suite_gpu(["2"]) == "2"
    assert suite_gpu.choose_suite_gpu(["0"]) is None
    assert suite_gpu.compute_indexes("0, [N/A], Disabled\n") == []
    assert suite_gpu.choose_suite_gpu([]) is None


def test_this_run_is_pinned_to_the_choice_the_rows_give():
    try:
        done = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,memory.total,display_active", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        done = None
    text = done.stdout if done is not None and done.returncode == 0 else ""
    visible = suite_gpu.VISIBLE if not suite_gpu.PRESET else ""
    expect = suite_gpu.choose_suite_gpu([], suite_gpu.PRESET, visible)
    if expect is None and not suite_gpu.PRESET and text:
        expect = suite_gpu.choose_suite_gpu(suite_gpu.compute_indexes(text), "")
    assert suite_gpu.CHOSEN == expect
    if expect is None:
        assert os.environ.get("STUDIO_GPU", "") == suite_gpu.PRESET
    else:
        assert os.environ["STUDIO_GPU"] == expect
        assert os.environ["CUDA_VISIBLE_DEVICES"] == expect
