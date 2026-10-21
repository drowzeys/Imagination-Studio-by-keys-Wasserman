"""Every test runs with a studio folder of its own: a test that imports the server without naming STUDIO_HOME must
never use (or move, app/studio_home.py) the person's real ~/ImaginationStudio or ~/TensorFoldStudio."""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp(prefix="studio-test-")) / "ImaginationStudio"))
# A display GPU is nvidia-smi's first row. Read a compute GPU instead. A Spark ([N/A] memory) is left unset.
_spec = importlib.util.spec_from_file_location("suite_gpu", Path(__file__).with_name("suite_gpu.py"))
_suite_gpu = importlib.util.module_from_spec(_spec)
sys.modules["suite_gpu"] = _suite_gpu
_spec.loader.exec_module(_suite_gpu)
_suite_gpu.pin_suite_gpu()
# nor walk the machine for models: the app starts that scan 20 s after it is imported (app/discover.py), and it would
# hold the scan's lock while tests/test_discover.py runs its own scans
os.environ.setdefault("STUDIO_DISCOVER_DELAY", "-1")
# nor start a model server of this machine's: NVIDIA Nemotron 3.5 Bolt as the Studio's engine (app/bolt.py) is a stand-in
# in tests/test_bolt.py, and everywhere else the brains' local server is the one a test names
os.environ.setdefault("BOLT_BACKEND", "external")
# nor ask for the studio's access key (app/access.py): tests/test_access.py turns it on for its own requests
os.environ.setdefault("STUDIO_ACCESS_KEY", "off")


@pytest.fixture
def spark_memory():
    """For a test that queues a job on Linux: the app takes Linux for a DGX Spark (128 GB) and its memory guard
    (spark/memory_guard.py) reads this machine's own /proc/meminfo, so a smaller Linux machine, such as a CI runner,
    refuses the job before the test's point. Skipped there; it runs on a Mac and on a Spark."""

    if not sys.platform.startswith("linux"):
        return
    try:
        total_kb = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemTotal:")))
    except (OSError, StopIteration, ValueError, IndexError):
        return
    if total_kb < 100 * 1024 * 1024:
        pytest.skip(f"this Linux machine has {total_kb / 1024 / 1024:.0f} GB: the app takes it for a DGX Spark and its "
                    "memory guard refuses the job before the test's point (runs on a Mac and on a Spark)")
