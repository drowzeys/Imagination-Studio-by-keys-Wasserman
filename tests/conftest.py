"""Every test runs with a studio folder of its own: a test that imports the server without naming STUDIO_HOME must
never use (or move, app/studio_home.py) the person's real ~/ImaginationStudio or ~/TensorFoldStudio."""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp(prefix="studio-test-")) / "ImaginationStudio"))
os.environ.setdefault("PREFIX", tempfile.mkdtemp(prefix="studio-test-runtime-"))
# These constants are read on the first server import, before individual test modules are collected.
# Never discover the person's existing ComfyUI on its real port.
os.environ.setdefault("SPARK_COMFY_PORT", "1")
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

@pytest.fixture(autouse=True)
def fresh_imported_hub():
    """Each test reuses an inactive imported app, not a process that was permanently shut down.

    Actual shutdown tests use isolated Hubs/processes. A TestClient lifespan must not
    leave this shared test singleton quiesced for unrelated tests or module fixtures.
    """
    def reopen():
        hub = getattr(sys.modules.get("server"), "hub", None)
        if hub is not None and not hub.started and not hub.local.active:
            hub.closing = hub.local.closing = False
    reopen()
    yield
    reopen()



@pytest.fixture
def spark_memory(monkeypatch):
    """A virtual 128 GB Spark for contract tests, independent of real models or the CI runner's RAM."""
    if not sys.platform.startswith("linux"):
        return
    import memory_guard
    monkeypatch.setattr(memory_guard, "meminfo", lambda: {"MemTotal": 124546, "MemAvailable": 118700})
    monkeypatch.setattr(memory_guard, "device", lambda **_: None)
