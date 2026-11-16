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


# ---------- Comfy Cloud (issue #185): the same panel with a key ----------

class Cloud(BaseHTTPRequestHandler):
    seen: list = []
    job: dict = {}
    refuse: int = 0

    def log_message(self, *a):
        pass

    def reply(self, body: bytes, kind="application/json", code=200, more=()):
        self.send_response(code); self.send_header("Content-Type", kind); self.send_header("Content-Length", str(len(body)))
        for name, value in more:
            self.send_header(name, value)
        self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        Cloud.seen.append((self.path, self.headers.get("X-API-Key"), body))
        if Cloud.refuse:
            return self.reply(b"{}", code=Cloud.refuse)
        self.reply(json.dumps({"name": "ref.png", "subfolder": "", "type": "input"} if self.path == "/api/upload/image" else {"prompt_id": JOB, "node_errors": {}}).encode())

    def do_GET(self):
        Cloud.seen.append((self.path, self.headers.get("X-API-Key"), b""))
        if self.path.startswith("/api/jobs/"):
            return self.reply(json.dumps(Cloud.job).encode())
        if self.path.startswith("/api/view?"):
            return self.reply(b"", code=302, more=[("Location", f"http://127.0.0.1:{self.server.server_address[1]}/signed/shot.mp4?sig=abc")])
        self.reply(MP4, "video/mp4")


@pytest.fixture()
def cloud(monkeypatch, comfy):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Cloud)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    Cloud.seen, Cloud.job, Cloud.refuse = [], {}, 0
    monkeypatch.setenv("STUDIO_COMFY_URL", f"http://127.0.0.1:{server.server_address[1]}")
    monkeypatch.setenv("STUDIO_COMFY_API_KEY", "comfy-secret-key")
    yield comfy
    server.shutdown()


def test_with_a_key_the_shot_goes_to_comfy_cloud_s_own_routes(cloud):
    profile = slate_comfy.profiles()["ref"]
    assert slate_comfy.submit(cloud, profile, "a boat at dawn", 7, ["uploads/face.png"]) == JOB
    (upload, key1, _), (route, key2, body) = Cloud.seen
    assert (upload, route) == ("/api/upload/image", "/api/prompt") and key1 == key2 == "comfy-secret-key"
    sent = json.loads(body)
    assert sent["prompt"]["6"]["inputs"]["text"] == "a boat at dawn" and sent["prompt"]["21"]["inputs"]["image"] == "ref.png"
    assert sent["extra_data"] == {"api_key_comfy_org": "comfy-secret-key"}  # what the cloud's partner nodes are paid with
    assert Comfy.seen == []  # and nothing went to the other address


def test_a_cloud_job_waits_fails_with_its_reason_or_lands_from_the_signed_address_without_the_key(cloud):
    profile = slate_comfy.profiles()["ref"]
    for waiting in ("pending", "in_progress", "queued_waiting"):
        Cloud.job = {"status": waiting}
        assert slate_comfy.result(cloud, profile, JOB) == {"state": "waiting"}
    Cloud.job = {"status": "failed", "execution_error": {"exception_message": "model not found"}}
    with pytest.raises(ValueError, match="Comfy Cloud render failed: model not found"):
        slate_comfy.result(cloud, profile, JOB)
    Cloud.job = {"status": "cancelled"}
    with pytest.raises(ValueError, match="was cancelled"):
        slate_comfy.result(cloud, profile, JOB)
    Cloud.job = {"status": "completed", "outputs": {"25": {"videos": [{"filename": "shot.mp4", "subfolder": "", "type": "output"}]}}}
    Cloud.seen = []
    done = slate_comfy.result(cloud, profile, JOB)
    assert done == {"state": "done", "path": f"uploads/comfy-{JOB}.mp4"} and (cloud / done["path"]).read_bytes() == MP4
    (status, key1, _), (view, key2, _), (signed, key3, _) = Cloud.seen
    assert status == "/api/jobs/" + JOB and view.startswith("/api/view?") and key1 == key2 == "comfy-secret-key"
    assert signed.startswith("/signed/") and key3 is None  # the key never follows the redirect


def test_the_key_is_only_sent_over_https_and_a_refusal_says_why(cloud, monkeypatch):
    for code, why in ((401, "refused the API key"), (402, "not enough credits"), (429, "subscription is not active")):
        Cloud.refuse = code
        with pytest.raises(ValueError, match=why) as said:
            slate_comfy.request("/prompt", {})
        assert "comfy-secret-key" not in str(said.value)
    Cloud.refuse = 400
    with pytest.raises(ValueError, match="did not take the request"):
        slate_comfy.request("/prompt", {})
    seen = len(Cloud.seen)
    monkeypatch.setenv("STUDIO_COMFY_URL", "http://comfy.example:8188")
    with pytest.raises(ValueError, match="only sent over https"):
        slate_comfy.request("/prompt", {})
    assert len(Cloud.seen) == seen  # nothing was sent


def test_the_panel_is_told_when_its_renders_go_to_the_cloud(cloud, monkeypatch):
    import slate

    assert slate.comfy_profiles() == {"ref": {"label": "One reference", "references": 1, "cloud": True}}
    monkeypatch.delenv("STUDIO_COMFY_API_KEY")
    assert slate.comfy_profiles() == {"ref": {"label": "One reference", "references": 1}}
