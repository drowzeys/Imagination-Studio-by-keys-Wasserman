"""Session exit stops admission and event streams without re-raising a handled signal."""
import os
import signal
import sys
import tempfile
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
os.environ.setdefault("STUDIO_GPU_LOCK", str(Path(tempfile.mkdtemp()) / "studio-gpu.lock.d"))
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402
import telemetry  # noqa: E402


def test_ctrl_c_stops_admission_and_event_streams_before_the_server_closes(monkeypatch):
    monkeypatch.setattr(server, "stopping", threading.Event())
    monkeypatch.setattr(telemetry, "STOPPING", threading.Event())
    observed = []
    server.begin_shutdown(lambda *_: observed.append((server.stopping.is_set(), telemetry.STOPPING.is_set())),
                          signal.SIGINT, None)
    assert observed == [(True, True)]
    with pytest.raises(server.HTTPException) as rejected:
        server.accepting_jobs()
    assert rejected.value.status_code == 503



def test_ctrl_c_is_not_raised_again_after_the_hooks():
    import os
    seen = []
    with server.catch_exit(lambda sig, _frame: seen.append(sig)):
        os.kill(os.getpid(), signal.SIGINT)
    assert seen == [signal.SIGINT]


