"""Slate shots on an operator's ComfyUI (app/slate_comfy.py): python3 -m pytest tests/test_slate_comfy.py. The
ComfyUI is a stand-in on a loopback port: nothing renders and no other machine is reached."""
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import slate_comfy  # noqa: E402

JOB = "0123abcd-0123-4abc-8def-0123456789ab"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


class Comfy(BaseHTTPRequestHandler):
    seen: list = []
    history: dict = {}

    def log_message(self, *a):
        pass

    def reply(self, body: bytes, kind="application/json"):
        self.send_response(200); self.send_header("Content-Type", kind); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        Comfy.seen.append((self.path, body))
        self.reply(json.dumps({"name": "ref.png", "subfolder": "in"} if self.path == "/upload/image" else {"prompt_id": JOB}).encode())

    def do_GET(self):
        Comfy.seen.append((self.path, b""))
        if self.path.startswith("/history/"):
            return self.reply(json.dumps(Comfy.history).encode())
        self.reply(MP4, "video/mp4")


@pytest.fixture()
def comfy(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Comfy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    Comfy.seen, Comfy.history = [], {}
    with tempfile.TemporaryDirectory() as scratch:
        home = Path(scratch) / "home"; (home / "uploads").mkdir(parents=True)
        (home / "uploads/face.png").write_bytes(b"\x89PNG" + b"0" * 32)
        flow = Path(scratch) / "flow.json"
        flow.write_text(json.dumps({"6": {"inputs": {"text": ""}}, "12": {"inputs": {"noise_seed": 0}}, "21": {"inputs": {"image": ""}}, "25": {"inputs": {}}}))
        book = Path(scratch) / "profiles.json"
        book.write_text(json.dumps({"ref": {"label": "One reference", "workflow": str(flow), "output": "25",
                                            "inputs": {"prompt": ["6", "text"], "seed": ["12", "noise_seed"], "references": [["21", "image"]]}}}))
        monkeypatch.setenv("STUDIO_COMFY_URL", f"http://127.0.0.1:{server.server_address[1]}")
        monkeypatch.setenv("STUDIO_COMFY_WORKFLOWS", str(book))
        yield home
    server.shutdown()


def test_nothing_is_offered_or_sent_until_both_settings_are_there(monkeypatch):
    monkeypatch.delenv("STUDIO_COMFY_URL", raising=False); monkeypatch.delenv("STUDIO_COMFY_WORKFLOWS", raising=False)
    assert slate_comfy.profiles() == {}
    monkeypatch.setenv("STUDIO_COMFY_URL", "http://127.0.0.1:8188")
    assert slate_comfy.profiles() == {}


@pytest.mark.parametrize("address", ["http://127.0.0.1:8190", "localhost:8190", "http://127.0.0.1:8191"])
def test_the_studios_own_comfyui_is_refused(monkeypatch, comfy, address):
    # its renders are queue jobs under the memory guard and the thermal gate: a workflow is never posted to it directly
    monkeypatch.delenv("SPARK_COMFY_PORT", raising=False); monkeypatch.delenv("MAC_COMFY_PORT", raising=False)
    monkeypatch.setenv("STUDIO_COMFY_URL", address)
    with pytest.raises(ValueError, match="this Studio's own ComfyUI"):
        slate_comfy.profiles()
    with pytest.raises(ValueError, match="this Studio's own ComfyUI"):
        slate_comfy.request("/prompt", {})
    monkeypatch.setenv("SPARK_COMFY_PORT", "8300"); monkeypatch.setenv("STUDIO_COMFY_URL", "http://127.0.0.1:8300")
    with pytest.raises(ValueError, match="this Studio's own ComfyUI"):
        slate_comfy.worker()


def test_a_shot_is_sent_with_its_prompt_seed_and_reference(comfy):
    profile = slate_comfy.profiles()["ref"]
    assert slate_comfy.submit(comfy, profile, "a boat at dawn", 7, ["uploads/face.png"]) == JOB
    (upload, sent), (route, body) = Comfy.seen
    assert upload == "/upload/image" and b"\x89PNG" in sent and b"face.png" not in sent  # the file, under a name of its own
    flow = json.loads(body)["prompt"]
    assert route == "/prompt" and flow["6"]["inputs"]["text"] == "a boat at dawn" and flow["12"]["inputs"]["noise_seed"] == 7
    assert flow["21"]["inputs"]["image"] == "in/ref.png"


def test_a_reference_is_only_a_picture_inside_the_studio_folder(comfy):
    profile = slate_comfy.profiles()["ref"]
    (comfy / "uploads/.hidden.png").write_bytes(b"x"); (comfy / "uploads/notes.txt").write_text("x")
    (comfy.parent / "outside.png").write_bytes(b"x")
    for bad in ("../outside.png", "uploads/.hidden.png", "uploads/notes.txt", "uploads/missing.png", str(comfy.parent / "outside.png")):
        with pytest.raises(ValueError, match="Select a Studio reference image"):
            slate_comfy.submit(comfy, profile, "x", 1, [bad])
    with pytest.raises(ValueError, match="requires 1 reference images"):
        slate_comfy.submit(comfy, profile, "x", 1, [])
    assert Comfy.seen == []  # nothing left the machine
    profile["inputs"]["prompt"] = ["6", "words"]
    with pytest.raises(ValueError, match="6.words does not exist"):
        slate_comfy.submit(comfy, profile, "x", 1, ["uploads/face.png"])
    assert [path for path, _ in Comfy.seen] == ["/upload/image"]  # and no workflow was queued


def test_a_result_waits_then_lands_once_under_uploads(comfy):
    profile = slate_comfy.profiles()["ref"]
    with pytest.raises(ValueError, match="Invalid ComfyUI job ID"):
        slate_comfy.result(comfy, profile, "../../etc/passwd")
    assert slate_comfy.result(comfy, profile, JOB) == {"state": "waiting"}
    Comfy.history = {JOB: {"status": {"status_str": "error", "messages": [["execution_error", {"exception_message": "out of memory"}]]}}}
    with pytest.raises(ValueError, match="out of memory"):
        slate_comfy.result(comfy, profile, JOB)
    Comfy.history = {JOB: {"status": {"status_str": "success"}, "outputs": {"25": {"images": [{"filename": "a.png", "type": "output"}]}}}}
    with pytest.raises(ValueError, match="did not produce an MP4"):
        slate_comfy.result(comfy, profile, JOB)
    Comfy.history = {JOB: {"status": {"status_str": "success"}, "outputs": {"25": {"gifs": [{"filename": "shot.mp4", "subfolder": "", "type": "output"}]}}}}
    done = slate_comfy.result(comfy, profile, JOB)
    assert done == {"state": "done", "path": f"uploads/comfy-{JOB}.mp4"} and (comfy / done["path"]).read_bytes() == MP4
    views = sum(path.startswith("/view?") for path, _ in Comfy.seen)
    assert slate_comfy.result(comfy, profile, JOB) == done and sum(path.startswith("/view?") for path, _ in Comfy.seen) == views  # fetched once
    assert not list((comfy / "uploads").glob("*.part"))
