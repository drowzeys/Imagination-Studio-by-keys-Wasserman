"""Requests that change something (app/server.py WriteGuard), the job form's types, the shot's sentence stops, a
cancel that reaches the renderer, and the local model server's address (app/slate.py). Run:
STUDIO_HOME=$(mktemp -d) python3 -m pytest tests/test_write_guard.py. No network: ComfyUI is a stand-in on 127.0.0.1."""

import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402
import slate  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


def uploads() -> set:
    return set((server.HOME / "uploads").glob("*"))


# ---------- the Origin and the Host ----------

def test_a_page_of_another_site_cannot_change_anything():
    before = uploads()
    for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"}, {"Origin": "http://testserver.evil.example"}, {"Origin": "http://[broken"},
                    {"Sec-Fetch-Site": "cross-site"}):
        reply = client.post("/api/upload", files={"file": ("a.png", PNG, "image/png")}, headers=headers)
        assert reply.status_code == 403 and reply.json()["error"], headers
        assert client.post("/api/trash/restore", headers=headers).status_code == 403
        assert client.post("/api/jobs/nope/cancel", headers=headers).status_code == 403
        assert client.delete("/api/library", headers=headers).status_code == 403
    assert uploads() == before  # nothing written


def test_the_studio_page_scripts_and_curl_still_can():
    for headers in ({}, {"Origin": "http://testserver"}, {"Origin": "https://testserver", "Sec-Fetch-Site": "same-origin"}):
        reply = client.post("/api/upload", files={"file": ("a.png", PNG, "image/png")}, headers=headers)
        assert reply.status_code == 200, (headers, reply.text)
    assert client.get("/api/jobs", headers={"Origin": "https://evil.example"}).status_code == 200  # reading is not guarded here


@pytest.fixture
def names_unknown(monkeypatch):
    """This computer's names not learned (yet), or no Tailscale to ask: one-word and *.ts.net names are taken."""
    monkeypatch.setattr(server, "OWN", {"labels": None, "tailnet": None})


@pytest.fixture
def names_known(monkeypatch):
    """This computer is studio-mac (Bonjour studio-mac.local), on the tailnet tail1234.ts.net."""
    monkeypatch.setattr(server, "OWN", {"labels": {"studio-mac"}, "tailnet": "tail1234.ts.net"})


@pytest.mark.parametrize("host,ok", [
    ("127.0.0.1:7870", True), ("localhost:7870", True), ("[::1]:7870", True), ("studio-mac.tail1234.ts.net", True),
    ("192.168.1.20:7870", True), ("10.0.0.5:7870", True), ("spark-5:7870", True), ("spark-5.local:7870", True),
    ("evil.example:7870", False), ("rebind.attacker.test", False), ("", False)])
def test_a_host_a_rebinding_page_could_hold_is_refused(host, ok, names_unknown):
    assert server.trusted_host(host) is ok
    reply = client.post("/api/jobs/nope/cancel", headers={"Host": host} if host else {"Host": ""})
    assert (reply.status_code == 403) is not ok, (host, reply.status_code)


@pytest.mark.parametrize("host,ok", [
    ("studio-mac:7870", True), ("STUDIO-MAC.local", True), ("studio-mac.tail1234.ts.net", True), ("spark-2.tail1234.ts.net", True),
    ("app.studio.localhost:7870", True), ("100.101.102.103:7870", True),
    ("evil.local:7870", False), ("intranet:7870", False), ("tail1234.ts.net.evil.example", False), ("ts.net.evil.example", False)])
def test_once_its_names_are_known_only_this_computers_names_are_taken(host, ok, names_known):
    assert server.trusted_host(host) is ok


def test_without_tailscale_to_ask_a_one_word_name_is_taken_as_before(monkeypatch):
    monkeypatch.setattr(server, "OWN", {"labels": {"studio-mac"}, "tailnet": None})  # no Tailscale here
    assert server.trusted_host("anything.tail9999.ts.net") and server.trusted_host("spark-5")  # as before
    assert not server.trusted_host("evil.local")  # a .local name is this computer's own once its names are known


def test_this_computers_names_are_learned_with_a_time_limit(monkeypatch):
    seen = []

    def run(command, **kwargs):
        seen.append((command[0], kwargs.get("timeout")))
        out = '{"Self": {"DNSName": "studio-mac.tail1234.ts.net.", "HostName": "Studio-Mac"}}' if "status" in command else "Studio-Mac\n"
        return subprocess.CompletedProcess(command, 0, out, "")

    monkeypatch.setattr(server.subprocess, "run", run)
    monkeypatch.setattr(server.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(server.socket, "gethostname", lambda: "studio-mac.lan")
    learned = server.learn_own_names()
    assert learned["labels"] >= {"studio-mac"} and learned["tailnet"] == "tail1234.ts.net"
    assert seen and all(timeout and timeout <= 3 for _, timeout in seen)


@pytest.mark.parametrize("path", ["/api/library", "/api/jobs", "/api/settings", "/api/state", "/files/settings.json", "/", "/api/nodes/graphs"])
def test_a_foreign_host_cannot_read_anything_either(path, names_unknown):
    """DNS rebinding: a page that points its own name at this machine reads with GET, so reads are held to the Host too."""
    reply = client.get(path, headers={"Host": "rebind.attacker.example"})
    assert reply.status_code == 403 and "Open the Studio" in reply.json()["error"], (path, reply.status_code)
    assert client.get("/api/jobs", headers={"Host": "127.0.0.1:7870"}).status_code == 200


def test_a_name_can_be_allowed(monkeypatch):
    monkeypatch.setenv("STUDIO_ALLOWED_HOSTS", "studio.example.org, other.example.org")
    assert server.trusted_host("studio.example.org:7870") and server.trusted_host("OTHER.example.org")
    assert not server.trusted_host("evil.example.org")


# ---------- body sizes ----------

def test_a_json_body_over_a_megabyte_is_refused_before_it_is_read():
    big = json.dumps({"kind": "video", "title": "x" * (server.JSON_LIMIT + 10), "params": {"prompt": "p"}})
    reply = client.post("/api/jobs", content=big, headers={"Content-Type": "application/json"})
    assert reply.status_code == 413 and "MB" in reply.json()["error"]


def test_a_body_without_a_length_is_counted_as_it_comes():
    def chunks():
        yield b'{"kind": "video", "title": "'
        for _ in range(20):
            yield b"x" * (1 << 16)
        yield b'"}'

    reply = client.post("/api/jobs", content=chunks(), headers={"Content-Type": "application/json"})
    assert reply.status_code == 413


def test_an_upload_over_the_limit_is_refused_and_not_kept(monkeypatch):
    monkeypatch.setattr(server, "UPLOAD_LIMIT", 4096)
    before = uploads()
    reply = client.post("/api/upload", files={"file": ("big.png", PNG + b"\0" * 8192, "image/png")})
    assert reply.status_code == 413
    assert uploads() == before
    assert client.post("/api/upload", files={"file": ("small.png", PNG, "image/png")}).status_code == 200


@pytest.mark.parametrize("kind", ["text/plain", "application/merge-patch+json", "application/vnd.api+json", None])
def test_every_body_is_held_to_the_limit_whatever_its_type(kind):
    big = json.dumps({"appearance": {"glow": False}, "pad": "x" * (server.JSON_LIMIT + 10)})
    reply = client.post("/api/settings", content=big, headers={"Content-Type": kind} if kind else {})
    assert reply.status_code == 413, (kind, reply.status_code)
    assert len(reply.content) < 1000  # the body is never echoed back

    def chunks():
        for _ in range(20):
            yield b"x" * (1 << 16)

    assert client.post("/api/nodes/check", content=chunks(), headers={"Content-Type": "text/plain"}).status_code == 413


def test_a_body_the_route_cannot_read_is_not_echoed_back():
    sent = "SECRET-" + "y" * 5000
    reply = client.post("/api/settings", content=sent, headers={"Content-Type": "text/plain"})
    assert reply.status_code == 422 and reply.json()["error"] and "SECRET" not in reply.text and len(reply.text) < 1000


def test_each_file_route_has_its_own_limit(monkeypatch):
    monkeypatch.setattr(server, "UPLOAD_LIMIT", 4096)
    monkeypatch.setattr(server, "ARCHIVE_LIMIT", 8192)
    pack = {"file": ("p.zip", b"\0" * 6000, "application/zip")}
    assert client.post("/api/upload", files={"file": ("a.png", PNG + b"\0" * 6000, "image/png")}).status_code == 413
    assert client.post("/api/motion/packs/import", files=pack).status_code != 413  # under its own, larger limit
    big = {"file": ("p.zip", b"\0" * 9000, "application/zip")}
    assert client.post("/api/motion/packs/import", files=big).status_code == 413
    assert client.post("/api/suite/import", files=big).status_code == 413
    # a file sent to a route that does not take one is held to the JSON limit
    monkeypatch.setattr(server, "JSON_LIMIT", 2048)
    assert client.post("/api/settings", files={"file": ("a.png", PNG + b"\0" * 4000, "image/png")}).status_code == 413


# ---------- hidden files, null bytes ----------

@pytest.fixture
def hidden_files():
    made = [server.HOME / ".studio-agents" / "tools.json", server.HOME / "renders" / ".cache" / "x.mp4", server.HOME / ".secret.png"]
    for path in made:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    yield made
    for path in made:
        path.unlink(missing_ok=True)


def test_hidden_files_are_never_served_listed_or_used(hidden_files):
    for path in (".studio-agents/tools.json", "renders/.cache/x.mp4", ".secret.png", "renders/../.studio-agents/tools.json", "%2Estudio-agents/tools.json"):
        assert client.get("/files/" + path).status_code in (400, 404), path
    listed = [item["path"] for item in client.get("/api/library").json()]
    assert not any("/." in "/" + p for p in listed)
    for path in (".studio-agents/tools.json", "renders/.cache/x.mp4"):
        assert client.get("/api/probe", params={"path": path}).status_code == 400
        assert client.delete("/api/library", params={"path": path}).status_code == 400
    reply = client.post("/api/jobs", json={"kind": "export", "params": {"clips": [{"path": "renders/.cache/x.mp4"}]}})
    assert reply.status_code == 400 and "hidden" in reply.json()["error"]
    with pytest.raises(HTTPException):
        server.inside(server.HOME / ".studio-agents" / "tools.json")


def test_an_agents_records_are_read_from_this_computer_only():
    task = server.HOME / "agent" / "t-guard"
    task.mkdir(parents=True, exist_ok=True)
    for name in ("log.txt", "stream.jsonl", "out.png"):
        (task / name).write_bytes(b"x")
    away = TestClient(server.app, client=("192.168.1.50", 50000))
    assert away.get("/files/agent/t-guard/log.txt").status_code == 403
    assert away.get("/files/agent/t-guard/stream.jsonl").status_code == 403
    assert away.get("/files/agent/t-guard/out.png").status_code == 200  # what it delivered is a Library file
    assert client.get("/files/agent/t-guard/log.txt").status_code == 200


@pytest.mark.parametrize("call", [
    lambda: client.get("/files/a%00b"), lambda: client.get("/api/probe", params={"path": "a\0b"}),
    lambda: client.delete("/api/library", params={"path": "renders/a\0b.mp4"}),
    lambda: client.post("/api/frame", json={"path": "renders/a\0b.mp4"}),
    lambda: client.post("/api/jobs", json={"kind": "export", "params": {"clips": [{"path": "a\0b.mp4"}]}}),
])
def test_a_null_byte_in_a_path_is_a_400_not_a_500(call):
    assert call().status_code in (400, 404)


# ---------- typed values on the other routes ----------

@pytest.mark.parametrize("route,body", [
    ("/api/frame", {"path": "renders/guard.mp4", "at": {}}), ("/api/frame", {"path": "renders/guard.mp4", "at": [1]}),
    ("/api/frame", {"path": "renders/guard.mp4", "at": "soon"}), ("/api/frame", {"path": "renders/guard.mp4", "at": True}),
    ("/api/estimate", {"seconds": "x"}), ("/api/estimate", {"seconds": [5]}), ("/api/estimate", {"references": "two"}),
    ("/api/estimate", {"frames": "many"}), ("/api/estimate", {"frames": 100}),
    ("/api/compose", {"fields": "x"}), ("/api/compose", {"fields": ["x"]}), ("/api/compose", {"fields": {}, "references": {}}),
    ("/api/compose", {"fields": {}, "seconds": "x"}), ("/api/compose", {"fields": {}, "references": 1.5}),
])
def test_a_value_of_the_wrong_kind_is_a_400_on_every_route(route, body, monkeypatch):
    clip = server.HOME / "renders" / "guard.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"x")
    monkeypatch.setattr(server, "media_tools", lambda: {"ffmpeg": True, "ffprobe": True})
    reply = client.post(route, json=body)
    assert reply.status_code == 400 and reply.json()["error"], (route, body, reply.status_code, reply.text)
    clip.unlink()


def test_the_right_kinds_still_pass():
    assert client.post("/api/estimate", json={"seconds": "5", "preset": "draft"}).status_code == 200
    assert client.post("/api/compose", json={"fields": {"scene": "a quay"}, "seconds": 8}).status_code == 200


# ---------- the job form's types ----------

@pytest.mark.parametrize("body", [
    {"kind": "video", "params": "x"}, {"kind": "video", "params": [1]}, {"kind": "video", "title": ["x"], "params": {"prompt": "p"}},
    {"kind": "long", "params": {"prompt": "p", "total_seconds": "abc"}}, {"kind": "long", "params": {"prompt": "p", "total_seconds": None}},
    {"kind": "video", "params": {"prompt": "p", "quality": "fh8", "seed": "abc"}},
    {"kind": "export", "params": {"clips": "x"}}, {"kind": "export", "params": {"clips": [{"path": "../../etc/passwd"}]}},
    {"kind": "export", "params": {"clips": [{"path": "renders/a.mp4", "in": "start"}]}}])
def test_a_setting_of_the_wrong_type_is_a_400_with_a_plain_reason(body):
    reply = client.post("/api/jobs", json=body)
    assert reply.status_code == 400, reply.text
    said = reply.json()["error"]
    assert said and "Traceback" not in said and "int()" not in said


# ---------- the shot's sentences ----------

def test_the_scene_and_the_action_are_two_sentences():
    prompt = server.compose({"scene": "A lighthouse seen from a window", "action": "The lamp turns slowly."}, False)
    assert "A lighthouse seen from a window. The lamp turns slowly." in prompt
    prompt = server.compose({"scene": "A lighthouse at night.", "camera": "The camera holds", "action": "The lamp turns"}, False)
    assert "A lighthouse at night. The camera holds. The lamp turns" in prompt and ".." not in prompt
    assert server.joined("A quay,", "a boat leaves") == "A quay, a boat leaves"
    assert server.joined("", "  ", "Only the action") == "Only the action"


# ---------- cancel reaches the renderer ----------

class Comfy(http.server.BaseHTTPRequestHandler):
    seen: list = []
    status = 200

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        Comfy.seen.append(self.path)
        self.send_response(Comfy.status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def comfy(monkeypatch):
    stand_in = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Comfy)
    threading.Thread(target=stand_in.serve_forever, daemon=True).start()
    Comfy.seen, Comfy.status = [], 200
    monkeypatch.setenv("SPARK_COMFY_URL", f"http://127.0.0.1:{stand_in.server_address[1]}")
    yield stand_in
    stand_in.shutdown()


def running_job(kind: str = "video") -> str:
    job_id = f"t{time.time_ns()}"
    server.jobs[job_id] = {"id": job_id, "kind": kind, "status": "running", "title": "t", "params": {}, "outputs": [],
                           "log": "", "created": time.time(), "started": time.time()}
    process = subprocess.Popen(["sleep", "30"], start_new_session=True)
    server.running.update(process=process, id=job_id)
    return job_id


def finish(job_id: str):
    process = server.running["process"]
    process.wait(timeout=5)
    server.running.update(process=None, id=None)
    server.jobs.pop(job_id, None)
    return process


def test_cancel_on_a_spark_stops_the_prompt_comfyui_is_running(comfy, monkeypatch):
    monkeypatch.setattr(server, "SPARK", True)
    job_id = running_job()
    reply = client.post(f"/api/jobs/{job_id}/cancel")
    assert reply.json()["status"] == "cancelled"
    assert Comfy.seen == ["/interrupt"]
    assert "ComfyUI stopped the prompt" in reply.json()["log"]
    assert finish(job_id).returncode is not None  # the script is stopped too, as before


def test_cancel_says_so_when_comfyui_does_not_answer(monkeypatch):
    monkeypatch.setattr(server, "SPARK", True)
    monkeypatch.setenv("SPARK_COMFY_URL", "http://127.0.0.1:9")  # nothing listens there
    job_id = running_job()
    began = time.time()
    reply = client.post(f"/api/jobs/{job_id}/cancel")
    assert time.time() - began < 5 and reply.json()["status"] == "cancelled"
    assert "ComfyUI did not answer the stop" in reply.json()["log"]
    finish(job_id)


def test_cancel_on_a_mac_or_of_a_job_without_comfyui_only_stops_the_script(comfy, monkeypatch):
    monkeypatch.setattr(server, "SPARK", False)
    job_id = running_job()
    assert client.post(f"/api/jobs/{job_id}/cancel").json()["status"] == "cancelled"
    finish(job_id)
    monkeypatch.setattr(server, "SPARK", True)
    job_id = running_job("export")
    client.post(f"/api/jobs/{job_id}/cancel")
    finish(job_id)
    assert Comfy.seen == []


# ---------- the local model server's address ----------

@pytest.mark.parametrize("given", ["", "127.0.0.1:11434", "http://localhost:1234/v1", "http://192.168.1.20:8000",
                                   "http://10.1.2.3:8080", "http://172.20.0.4:8000", "http://100.101.102.103:8000",
                                   "http://[fd7a:115c:a1e0::1]:8000", "http://[::1]:8000", "http://gpu-box:8000",
                                   "https://gpu-box.tail1234.ts.net", "http://gpu-box.local:8000"])
def test_a_nearby_model_server_is_taken(given):
    assert slate.nearby_endpoint(given) == given.strip()


@pytest.mark.parametrize("given", ["http://example.com", "https://api.example.org/v1", "http://169.254.169.254/latest",
                                   "ftp://127.0.0.1", "file:///etc/passwd", "http://user:pw@127.0.0.1:8000",
                                   "http://8.8.8.8", "http://[2001:db8::1]:80", "gopher://localhost"])
def test_anything_else_is_refused(given):
    with pytest.raises(HTTPException) as refused:
        slate.nearby_endpoint(given)
    assert refused.value.status_code == 400
    assert client.get("/api/slate/brain", params={"endpoint": given}).status_code == 400


def test_the_slate_brain_runs_only_from_this_computer_and_the_studio_page():
    away = TestClient(server.app, client=("10.0.0.5", 50000))
    assert away.post("/api/slate/brain", json={"backend": "claude", "prompt": "ready"}).status_code == 403
    sent = client.post("/api/slate/brain", content=json.dumps({"prompt": "ready"}), headers={"Content-Type": "text/plain"})
    assert sent.status_code in (415, 422)  # FastAPI refuses a body that is not JSON before the route runs
