"""Selected estimates retain the actual worker endpoint's measured-route exclusions."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import os
from pathlib import Path
import sys
import tempfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "studio"))

import server
import sparks
from test_worker_admission import isolated_queue, card_guard


@pytest.fixture
def selected_estimator(isolated_queue, monkeypatch, tmp_path):
    client, worker_hub, address = isolated_queue
    monkeypatch.setattr(server, "card_face", lambda: False)
    monkeypatch.setattr(server, "SPARK_ENGINE_LIB", tmp_path / "not-installed-engine")
    monkeypatch.setattr(server, "memory_of", lambda *args, **kwargs: card_guard(98304))

    class WorkerEndpoint(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            answer = client.post(self.path, content=body, headers={"Content-Type": "application/json"})
            self.send_response(answer.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(answer.content)))
            self.end_headers()
            self.wfile.write(answer.content)

        def log_message(self, *args):
            pass

    endpoint = ThreadingHTTPServer(("127.0.0.1", 0), WorkerEndpoint)
    thread = threading.Thread(target=endpoint.serve_forever, daemon=True)
    thread.start()
    info = {**worker_hub.info(), "loras": ["synthetic-style"]}
    coordinator = sparks.Hub(tmp_path / "coordinator", server.PACK, True, {}, [], threading.RLock(),
                             threading.Event(), lambda: dict(info), lambda path: tmp_path / path,
                             lambda job, worker: None)
    coordinator.keep({"workers": [{"address": address, "name": "Synthetic worker", "enabled": True}], "sparks": 1})
    coordinator.state(address).update(state="ready", info=info)
    monkeypatch.setattr(coordinator, "connect", lambda _: "http://127.0.0.1:" + str(endpoint.server_port))
    params = {"preset": "small", "quality": "fh8", "frames": 124}
    try:
        yield coordinator, address, params
    finally:
        endpoint.shutdown()
        endpoint.server_close()
        thread.join(timeout=3)


def test_selected_text_clip_does_not_acquire_image_conditioned_timing(selected_estimator):
    coordinator, address, params = selected_estimator
    target = {"worker": address, "policy": "require"}
    text = coordinator.estimate_worker({**params, "text_only": True}, target)
    image = coordinator.estimate_worker({**params, "text_only": False}, target)
    inferred_text = coordinator.estimate_worker(params, target)
    inferred_image = coordinator.estimate_worker({**params, "image": "uploads/synthetic.png"}, target)
    assert 0 < text["seconds"] < image["seconds"]
    assert inferred_text["seconds"] < inferred_image["seconds"]


@pytest.mark.parametrize("media", [{"reference_videos": ["uploads/synthetic.mp4"]},
                                    {"reference_audios": ["uploads/synthetic.wav"]},
                                    {"referenced": True, "references": 1, "ref_media": True}])
def test_selected_media_reference_keeps_unmeasured_timing(selected_estimator, media):
    coordinator, address, params = selected_estimator
    target = {"worker": address, "policy": "require"}
    assert coordinator.estimate_worker(params, target)["seconds"] is not None
    assert coordinator.estimate_worker({**params, "referenced": True, "references": 1}, target)["seconds"] is not None
    assert coordinator.estimate_worker({**params, **media}, target)["seconds"] is None


@pytest.mark.parametrize("adapter", [{"loras": [{"id": "synthetic-style", "weight": 1}]}, {"with_lora": True}, {"loras": True}])
def test_selected_adapter_route_has_no_borrowed_measured_time(selected_estimator, adapter):
    coordinator, address, params = selected_estimator
    target = {"worker": address, "policy": "require"}
    assert coordinator.estimate_worker(params, target)["seconds"] is not None
    assert coordinator.estimate_worker({**params, **adapter}, target)["seconds"] is None
