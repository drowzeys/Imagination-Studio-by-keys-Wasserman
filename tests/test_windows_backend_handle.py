"""Backend ownership is validated by job membership and creation identity, not a reused PID."""
from types import SimpleNamespace
import threading

import pytest

import windows_backend_handle as backend


@pytest.mark.parametrize("created,contained,passes", [(123, True, True), (999, True, False), (123, False, False)])
def test_backend_handle_rejects_reuse_or_foreign_process_and_closes_handles(monkeypatch, created, contained, passes):
    closed, reads = [], []
    api = SimpleNamespace(OpenProcess=lambda *_: 100, CloseHandle=lambda handle: closed.append(handle) or True)
    monkeypatch.setattr(backend, "kernel", lambda: api)
    monkeypatch.setattr(backend, "creation_time", lambda handle: created)
    monkeypatch.setattr(backend, "process_memory", lambda handle: reads.append(handle) or {"private_commit_bytes": 2048})
    def membership(handle, job, value):
        value._obj.value = contained
        return True
    job = SimpleNamespace(lock=threading.RLock(), handle=200, api=SimpleNamespace(IsProcessInJob=membership))
    if passes:
        retained = backend.open_owned(job, {"pid": 42, "created": 123})
        assert retained.read()["private_commit_bytes"] == 2048 and reads == [100]
        retained.close()
        retained.close()
    else:
        with pytest.raises(RuntimeError):
            backend.open_owned(job, {"pid": 42, "created": 123})
    assert closed == [100]
