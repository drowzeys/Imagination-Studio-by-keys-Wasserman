"""Brains and agents (app/agents.py): python3 -m pytest tests/test_agents.py. No network, no sign-ins: the CLIs, ssh,
scp and the MCP servers are small stand-ins written into a folder put first on PATH."""
import http.server
import json
import os
import signal
import socket
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
import agents  # noqa: E402
import slate  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
PY = sys.executable

CLAUDE = r'''
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
if args == ["--version"]:
    print("9.9.9 (Claude Code)"); sys.exit(0)
prompt = sys.stdin.read()
Path(os.environ["FAKE_LOG"]).open("a").write(json.dumps({"cli": "claude", "args": args, "cwd": os.getcwd(), "stdin": prompt}) + "\n")
if os.environ.get("FAKE_MODE") == "expired":
    print(json.dumps({"type": "result", "is_error": True, "result": "Not logged in · Please run /login"})); sys.exit(1)
if "stream-json" in args:
    session = args[args.index("--session-id") + 1] if "--session-id" in args else args[args.index("--resume") + 1] if "--resume" in args else "s0"
    if "--resume" in args and os.environ.get("FAKE_MODE") == "lost":
        print(json.dumps({"type": "result", "is_error": True, "result": f"No conversation found with session ID: {session}"})); sys.exit(1)
    print(json.dumps({"type": "system", "subtype": "init", "session_id": session, "mcp_servers": [{"name": "fake", "status": "connected"}]}), flush=True)
    print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Building the set."},
                      {"type": "tool_use", "name": "mcp__fake__make", "input": {}}]}}), flush=True)
    print(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": [{"type": "text", "text": json.dumps(
        {"id": "job1", "kind": "video", "status": "queued", "title": "agent: clip", "outputs": []})}]}]}}), flush=True)
    time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
    Path("render.png").write_bytes(b"png")
    said = "Made render.png" if "ask me" not in prompt else "I can make it two ways.\n\n[question]\nHow long should the clip be?\n- 5 s\n- 10 s"
    print(json.dumps({"type": "result", "is_error": False, "session_id": session, "result": said}), flush=True)
    sys.exit(0)
print(json.dumps({"type": "result", "is_error": False, "result": "ready" if "ready" in prompt else "?"}))
'''
CODEX = r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli 9.9"); sys.exit(0)
prompt = sys.stdin.read()
Path(os.environ["FAKE_LOG"]).open("a").write(json.dumps({"cli": "codex", "args": args, "cwd": os.getcwd(), "stdin": prompt}) + "\n")
if os.environ.get("FAKE_MODE") == "old":
    print(json.dumps({"type": "error", "message": json.dumps({"error": {"message": "The 'x' model is not supported when using Codex with a ChatGPT account."}})}))
    sys.exit(1)
if "workspace-write" in args or 'sandbox_mode="workspace-write"' in args:
    print(json.dumps({"type": "item.started", "item": {"type": "mcp_tool_call", "server": "fake", "tool": "make"}}))
    print(json.dumps({"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "fake", "tool": "make", "result": {
        "content": [{"type": "text", "text": json.dumps({"id": "job2", "kind": "scout", "status": "done", "outputs": ["scouts/a/s1.png"]})}]}}}))
    Path("set.json").write_text("{}")
print(json.dumps({"type": "thread.started", "thread_id": "codex-thread-1"}))
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ready"}}))
'''
GEMINI = r'''
import json, sys
if sys.argv[1:] == ["--version"]:
    print("0.9.0"); sys.exit(0)
sys.stdin.read()
print(json.dumps({"response": "ready", "stats": {}}))
'''
GROK = r'''
import sys
args = sys.argv[1:]
if args == ["--version"]:
    print("grok 1.0"); sys.exit(0)
assert args[:2] == ["--prompt-file", "/dev/stdin"], args
print("ready" if "ready" in open("/dev/stdin").read() else "?")
'''
HERMES = r'''
import json, os, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("Hermes Agent v0.21.5 (2026.9.24)"); sys.exit(0)
prompt = "" if "-z" in args else sys.stdin.read()
Path = __import__("pathlib").Path
Path(os.environ["FAKE_LOG"]).open("a").write(json.dumps({"cli": "hermes", "args": args, "cwd": os.getcwd(), "stdin": prompt}) + "\n")
if "-z" in args:
    print("ready" if "ready" in args[args.index("-z") + 1] else "?"); sys.exit(0)
if os.environ.get("FAKE_MODE") == "hermes-nologin":
    sys.stderr.write("Error: no inference provider configured. Run hermes setup.\n"); sys.exit(1)
session = args[args.index("--resume") + 1] if "--resume" in args else "20261018_120000_abcdef"
print("Warning: Unknown toolsets: messaging")
print(json.dumps({"type": "system", "subtype": "init", "model": "m", "session_id": session}))
print(json.dumps({"type": "text", "text": "ready"}))
print(json.dumps({"type": "result", "session_id": session, "exit_code": 0, "text": "ready" if "ready" in prompt else "Here is a plan."}))
'''
# ssh that runs the remote command here, in a home of its own: what a second machine would do
SSH = r'''
import os, subprocess, sys
from pathlib import Path
args, at = sys.argv[1:], 0
while args[at] in ("-o", "-p"):
    at += 2
host, rest = args[at], args[at + 1:]
if rest and rest[0] == "--":
    rest = rest[1:]
Path(os.environ["FAKE_LOG"]).open("a").write('{"ssh": "%s"}\n' % host)
if host.startswith("down"):
    sys.stderr.write("ssh: connect to host down: Connection refused\n"); sys.exit(255)
home = os.environ["FAKE_REMOTE_HOME"]
os.makedirs(home, exist_ok=True)
sys.exit(subprocess.call(["/bin/sh", "-c", " ".join(rest)], env={**os.environ, "HOME": home}))
'''
SCP = r'''
import os, shutil, sys
args = [a for a in sys.argv[1:] if a not in ("-q", "-r")]
clean, at = [], 0
while at < len(args):
    if args[at] in ("-o", "-P"):
        at += 2; continue
    clean.append(args[at]); at += 1
*sources, target = clean
destination = target.split(":", 1)[1] if ":" in target else target
for source in sources:
    source = source.split(":", 1)[1] if ":" in source and not os.path.exists(source) else source
    if os.path.isdir(source):
        shutil.copytree(source, destination, dirs_exist_ok=True)
    else:
        shutil.copy(source, destination)
'''
MCP = r'''
import json, os, sys
if os.environ.get("FAKE_MCP") == "mute":
    sys.stdin.read()
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    if message["method"] == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}}
    elif message["method"] == "tools/list":
        result = {"tools": [{"name": "get_scene_info"}, {"name": "execute_blender_code"}]}
    else:
        result = {}
    print("a stray line a server printed")
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
'''
LISTENER = r'''
import socket, sys, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
time.sleep(0.5); s.bind(("127.0.0.1", int(sys.argv[1]))); s.listen(); time.sleep(60)
'''


def script(folder: Path, name: str, body: str) -> Path:
    path = folder / name
    path.write_text(f"#!{PY}\n{body}")
    path.chmod(0o755)
    return path


@pytest.fixture
def fake(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in (("claude", CLAUDE), ("codex", CODEX), ("gemini", GEMINI), ("grok", GROK), ("hermes", HERMES), ("ssh", SSH), ("scp", SCP)):
        script(bin_, name, body)
    script(tmp_path, "mcp.py", MCP)
    script(tmp_path, "listen.py", LISTENER)
    log = tmp_path / "calls.jsonl"
    log.write_text("")
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}/usr/bin{os.pathsep}/bin")
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("FAKE_REMOTE_HOME", str(tmp_path / "remote-home"))
    monkeypatch.setattr(slate, "CLI_DIRS", ())
    monkeypatch.setattr(slate, "find_local", lambda given="": (None, []))
    monkeypatch.setattr(agents, "MAC_CODEX", ())
    monkeypatch.setattr(agents.shutil, "which", lambda name, path=None, _w=agents.shutil.which: None if name == "rsync" else _w(name, path=path))
    shutil_rmtree(agents.HOME / ".studio-agents")
    (agents.HOME / "settings.json").unlink(missing_ok=True)
    agents.probes.clear()
    agents.answering.clear()
    agents.threads.clear()
    for name in ("STUDIO_DEFAULT_BRAIN", "STUDIO_LOCAL_ENDPOINT", "STUDIO_LOCAL_MODEL"):
        monkeypatch.delenv(name, raising=False)
    yield {"bin": bin_, "log": log, "tmp": tmp_path, "calls": lambda: [json.loads(l) for l in log.read_text().splitlines()]}
    for task in list(agents.tasks.values()):  # nothing of one test runs into the next
        if task["status"] in ("queued", "running"):
            agents.cancel(task["id"])
    ends = time.time() + 10
    while agents.running["id"] and time.time() < ends:
        time.sleep(0.05)
    shutil_rmtree(agents.HOME / ".studio-agents")
    (agents.HOME / "settings.json").unlink(missing_ok=True)


def shutil_rmtree(path: Path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


def post(path: str, body: dict | None = None, **kwargs):
    return client.post(path, json=body if body is not None else {}, **kwargs)


def wait_for(task_id: str, statuses=("done", "failed", "cancelled"), seconds: float = 20) -> dict:
    ends = time.time() + seconds
    while time.time() < ends:
        task = client.get(f"/api/agent/tasks/{task_id}").json()
        if task["status"] in statuses:
            return task
        time.sleep(0.1)
    raise AssertionError(task)


def test_brains_are_found_with_their_versions(fake):
    (fake["bin"] / "grok").unlink()
    answer = client.get("/api/brains").json()
    found = {b["id"]: b for b in answer["backends"]}
    assert list(found) == ["claude", "codex", "gemini", "grok", "hermes", "local"]
    assert found["hermes"]["found"] and found["hermes"]["version"].startswith("Hermes Agent") and found["hermes"]["label"] == "Hermes"
    assert found["hermes"]["harness"] is False and found["hermes"]["session"] and "hermes setup" in found["hermes"]["signin"]
    assert found["claude"]["harness"] and found["codex"]["harness"] and not found["gemini"]["harness"] and found["local"]["harness"] is False
    assert found["claude"]["found"] and found["claude"]["version"] == "9.9.9 (Claude Code)" and found["claude"]["where"] == "here"
    assert found["claude"]["path"] == str(fake["bin"] / "claude") and "claude auth login" in found["claude"]["signin"]
    assert found["codex"]["label"] == "Codex · ChatGPT sign-in" and found["gemini"]["found"]
    assert found["grok"]["found"] is False and found["local"]["kind"] == "http" and found["local"]["found"] is False
    assert answer["default"] == "claude" and answer["settings"]["remote"] == {"host": "", "backends": [], "studio_host": ""}
    # Slate's own status names the new brains too
    status = client.get("/api/slate/brain").json()
    assert status["claude"] and status["gemini"] and status["grok"] is False and "local" in status


def test_a_brain_over_ssh_is_looked_for_on_that_machine(fake):
    assert post("/api/brains/settings", {"remote": {"host": "user@workstation-2", "backends": ["claude", "codex"]}}).status_code == 200
    found = {b["id"]: b for b in client.get("/api/brains").json()["backends"]}
    assert found["claude"]["where"] == "ssh:user@workstation-2" and found["claude"]["found"] and found["claude"]["version"] == "9.9.9 (Claude Code)"
    assert found["gemini"]["where"] == "here"
    assert sum(1 for c in fake["calls"]() if c.get("ssh")) == 1  # both CLIs in one ssh call
    client.get("/api/brains")
    assert sum(1 for c in fake["calls"]() if c.get("ssh")) == 1  # kept for a minute
    # a machine that does not answer says so, and the page still gets an answer at once
    post("/api/brains/settings", {"remote": {"host": "down-host"}})
    found = {b["id"]: b for b in client.get("/api/brains").json()["backends"]}
    assert found["claude"]["found"] is False and "ssh to down-host failed" in found["claude"]["error"]
    assert "`ssh down-host true`" in found["claude"]["error"] and "Settings → Connections" in found["claude"]["error"]  # what to do about it


@pytest.mark.parametrize("host", ["mac; rm -rf ~", "a b", "-oProxyCommand=x", "host$(id)", "user@@host", "-Jevil@jump", "-oProxyCommand=x@host", "h:port", "x" * 400, "`id`", 5])
def test_bad_hosts_are_refused(fake, host):
    answer = post("/api/brains/settings", {"remote": {"host": host, "backends": ["claude"]}})
    assert answer.status_code == 400 and "host" in answer.json()["error"]
    assert client.get("/api/brains").json()["settings"]["remote"]["host"] == ""


def test_settings_are_saved_privately_and_checked(fake):
    good = post("/api/brains/settings", {"default": "gemini", "remote": {"host": "user@workstation.local:2222", "backends": ["claude"],
                                                                        "studio_host": "user@studio-box"},
                                         "local_endpoint": "http://127.0.0.1:11434", "local_model": "qwen3:8b"}).json()["settings"]
    assert good == {"default": "gemini", "remote": {"host": "user@workstation.local:2222", "backends": ["claude"], "studio_host": "user@studio-box"},
                    "local_endpoint": "http://127.0.0.1:11434", "local_model": "qwen3:8b", "agent": ""}
    file = agents.HOME / ".studio-agents/settings.json"
    assert json.loads(file.read_text()) == good and (file.stat().st_mode & 0o777) == 0o600
    assert post("/api/brains/settings", {"default": "gpt"}).status_code == 400
    assert post("/api/brains/settings", {"agent": "gemini"}).status_code == 400  # the Agent room's default drives the Studio's tools
    assert post("/api/brains/settings", {"remote": {"backends": ["local"]}}).status_code == 400
    assert post("/api/brains/settings", {"local_endpoint": "http://x; rm"}).status_code == 400
    assert agents.ssh_of("user@workstation.local:2222")[-3:] == ["-p", "2222", "user@workstation.local", "--"][-3:] and "-p" in agents.ssh_of("user@workstation.local:2222")


def test_ask_each_brain(fake):
    for backend in ("claude", "codex", "gemini", "grok"):
        assert agents.ask(backend, "Be terse.", "Reply with the single word: ready") == "ready", backend
    claude = next(c for c in fake["calls"]() if c.get("cli") == "claude")
    assert claude["args"][:6] == ["-p", "--output-format", "json", "--tools", "", "--append-system-prompt"] and "--model" not in claude["args"]
    agents.ask("claude", "s", "ready", tier="fast")
    assert [c for c in fake["calls"]() if c.get("cli") == "claude"][-1]["args"][-2:] == ["--model", "haiku"]
    codex = next(c for c in fake["calls"]() if c.get("cli") == "codex")
    assert codex["args"] == ["exec", "--skip-git-repo-check", "--sandbox", "read-only", "--json", "-"]
    # the endpoint test answers the same way
    test = post("/api/brains/test", {"backend": "gemini"}).json()
    assert test["ok"] and test["text"] == "ready" and test["error"] is None and test["elapsedMs"] >= 0


def test_sign_in_trouble_says_what_to_do(fake, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "expired")
    with pytest.raises(RuntimeError, match="claude auth login"):
        agents.ask("claude", "", "ready")
    monkeypatch.setenv("FAKE_MODE", "old")
    with pytest.raises(RuntimeError, match="update it"):
        agents.ask("codex", "", "ready")
    (fake["bin"] / "grok").unlink()
    answer = post("/api/brains/test", {"backend": "grok"}).json()
    assert answer["ok"] is False and "not installed" in answer["error"]


def test_a_brain_over_ssh_answers(fake):
    post("/api/brains/settings", {"remote": {"host": "user@workstation", "backends": ["claude", "codex"]}})
    assert agents.ask("claude", "It's a system prompt with 'quotes' and $HOME", "ready") == "ready"
    assert agents.ask("codex", "", "ready") == "ready"
    calls = fake["calls"]()
    claude = next(c for c in calls if c.get("cli") == "claude" and "-p" in c["args"])
    assert claude["args"][6] == "It's a system prompt with 'quotes' and $HOME"  # quoted for the remote shell, unchanged
    assert claude["cwd"].startswith("/") and not Path(claude["cwd"]).exists()  # an empty folder there, removed after
    assert any(c.get("ssh") == "user@workstation" for c in calls)


def test_the_local_server(fake, monkeypatch):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.reply({"data": [{"id": "qwen3:8b"}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.reply({"choices": [{"message": {"content": f"ready from {body['model']}"}}]})

        def reply(self, value):
            data = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(slate, "find_local", lambda given="": (slate.endpoint_of(given), slate.local_models(slate.endpoint_of(given))) if given else (None, []))
    try:
        url = f"http://127.0.0.1:{httpd.server_port}"
        assert agents.ask("local", "s", "p", endpoint=url) == "ready from qwen3:8b"
        post("/api/brains/settings", {"local_endpoint": url, "local_model": "other"})
        # "other" is set, but the server runs only qwen3:8b: that is what is asked for and shown, and the page says so
        assert agents.ask("local", "s", "p") == "ready from qwen3:8b"
        local = client.get("/api/brains").json()["backends"][-1]
        assert local["found"] and local["models"] == ["qwen3:8b"] and local["endpoint"] == url + "/v1"
        assert local["model"] == "qwen3:8b" and local["label"] == "qwen3:8b (local)" and local["set_model"] == "other" and local["set_missing"]
    finally:
        httpd.shutdown()


def test_a_local_server_that_answers_is_the_default_until_one_is_chosen(fake, monkeypatch):
    up = {"http://studio-box:8091": ["nvidia-nemotron-3.5-bolt"]}
    asked = []
    monkeypatch.setattr(slate, "find_local", lambda given="": asked.append(given) or (
        (slate.endpoint_of(given), up[given]) if given in up else (None, [])))
    # nothing set: Claude Code, as before, and no address is guessed
    assert agents.saved_settings()["default"] == "claude" and agents.default_brain()["backend"] == "claude" and asked == []
    # the set server answers: it leads, for a new film too, and the page is told it was not chosen
    post("/api/brains/settings", {"local_endpoint": "http://studio-box:8091"})
    assert agents.default_brain() == {"backend": "local", "endpoint": "http://studio-box:8091", "model": ""}
    assert slate.blank("A film")["defaults"]["brain"] == "auto" and slate.resolve_brain("auto")[0] == "local"
    assert client.get("/api/slate/brain").json()["default"] == "local"
    answer = client.get("/api/brains").json()
    assert answer["default"] == "local" and answer["automatic"] is True
    file = agents.HOME / ".studio-agents/settings.json"
    assert "default" not in json.loads(file.read_text())  # an automatic default is never written down as a choice
    # asked once a minute, not on every look
    calls = len(asked)
    agents.saved_settings(), agents.saved_settings()
    assert len(asked) == calls
    # a brain the user picks always wins, even with the local server up
    post("/api/brains/codex/default")
    assert agents.default_brain()["backend"] == "codex" and client.get("/api/brains").json()["automatic"] is False
    assert json.loads(file.read_text())["default"] == "codex"
    # and an empty choice goes back to automatic
    assert post("/api/brains/settings", {"default": ""}).json()["settings"]["default"] == "local"
    # the server stops answering: back to Claude Code
    up.clear()
    post("/api/brains/settings", {"local_endpoint": "http://studio-box:8091"})
    assert agents.default_brain()["backend"] == "claude"


def test_the_environment_choice_wins_over_the_automatic_default(fake, monkeypatch):
    monkeypatch.setattr(slate, "find_local", lambda given="": (slate.endpoint_of(given), ["some-model"]) if given else (None, []))
    monkeypatch.setenv("STUDIO_LOCAL_ENDPOINT", "http://studio-box:8091")
    assert agents.default_brain()["backend"] == "local"
    monkeypatch.setenv("STUDIO_DEFAULT_BRAIN", "gemini")
    assert agents.default_brain()["backend"] == "gemini" and agents.default_automatic() is False


def test_the_local_brain_is_named_by_its_model(fake, monkeypatch):
    assert agents.local_label(["NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB.gguf"]) == "NVIDIA Nemotron 3.5 Bolt (local)"
    assert agents.local_label(["nvidia-nemotron-3.5-bolt", "qwen3:8b"], "qwen3:8b") == "qwen3:8b (local)"  # it answers with Qwen
    assert agents.local_label(["qwen3:8b"]) == "qwen3:8b (local)" and agents.local_label([]) == "Local model server"
    # a name set in Settings that the server no longer runs: the page shows, and the Studio asks for, what it does run
    assert agents.local_label(["DeepSeek-V4.1-Flash"], "GLM-5.3-Flash") == "DeepSeek-V4.1-Flash (local)"
    assert agents.answering_model(["DeepSeek-V4.1-Flash"], "GLM-5.3-Flash") == "DeepSeek-V4.1-Flash"
    assert agents.answering_model(["a", "b"], "b") == "b" and agents.answering_model([], "GLM") == "GLM"
    monkeypatch.setattr(slate, "find_local", lambda given="": ("http://studio-box:8091/v1", ["nvidia-nemotron-3.5-bolt"]))
    local = client.get("/api/brains").json()["backends"][-1]
    assert local["label"] == "NVIDIA Nemotron 3.5 Bolt (local)" and local["named"] is True
    assert client.get("/api/slate/brain").json()["localLabel"] == "NVIDIA Nemotron 3.5 Bolt (local)"


def test_slate_brain_goes_through_agents(fake):
    answer = post("/api/slate/brain", {"backend": "gemini", "system": "s", "prompt": "Reply ready", "tier": "top"}).json()
    assert answer["ok"] and answer["text"] == "ready"
    assert post("/api/slate/brain", {"backend": "grok", "prompt": "ready"}).json()["text"] == "ready"
    assert post("/api/slate/brain", {"backend": "nonsense", "prompt": "ready", "expectJson": False}).json()["text"] == "ready"  # Claude, as before
    post("/api/brains/settings", {"remote": {"host": "user@workstation", "backends": ["claude"]}})
    assert post("/api/slate/brain", {"backend": "claude", "prompt": "ready"}).json()["text"] == "ready"
    assert any(c.get("ssh") == "user@workstation" for c in fake["calls"]())


def fake_tool(fake, **more) -> dict:
    return {"id": "fake", "label": "Fake", "command": [PY, str(fake["tmp"] / "mcp.py")], "env": {"FAKE_X": "1"}, **more}


def test_tools_are_listed_saved_and_checked(fake, monkeypatch):
    listed = client.get("/api/agent/tools").json()["tools"]
    assert [t["id"] for t in listed] == ["blender", "blockout", "studio"]
    blender = listed[0]
    assert blender["command"] == ["uvx", "blender-mcp"] and blender["env"] == {"BLENDER_HOST": "127.0.0.1", "BLENDER_PORT": "9876"}
    assert listed[2]["command"][1].endswith("mcp/studio-mcp.py")
    saved = post("/api/agent/tools", fake_tool(fake)).json()["tools"]
    assert [t["id"] for t in saved] == ["blender", "blockout", "studio", "fake"]
    assert (agents.HOME / ".studio-agents/tools.json").stat().st_mode & 0o777 == 0o600
    for bad in ({"id": "Bad Id", "command": ["x"]}, {"id": "x", "command": "uvx blender-mcp"}, {"id": "x", "command": ["x"], "via": "cloud"},
                {"id": "x", "command": ["x"], "via": "ssh", "host": "a;b"}, {"id": "x", "command": ["x"], "env": {"A B": "1"}}):
        assert post("/api/agent/tools", bad).status_code == 400, bad
    check = post("/api/agent/tools/fake/check").json()
    assert check["ok"] and check["tools"] == ["get_scene_info", "execute_blender_code"] and check["server"]["name"] == "fake"
    # the same server on another machine, over ssh
    post("/api/agent/tools", fake_tool(fake, via="ssh", host="user@studio-box"))
    assert post("/api/agent/tools/fake/check").json()["tools"] == ["get_scene_info", "execute_blender_code"]
    # one that never answers is stopped, and says so
    monkeypatch.setattr(agents, "CHECK_TIMEOUT", 1)
    monkeypatch.setenv("FAKE_MCP", "mute")
    post("/api/agent/tools", fake_tool(fake))
    mute = post("/api/agent/tools/fake/check").json()
    assert mute["ok"] is False and "no answer in 1 seconds" in mute["error"]
    assert post("/api/agent/tools/nothing/check").status_code == 404


def test_the_studio_mcp_server_shakes_hands():
    tool = {"id": "studio", "label": "Studio", "command": [PY, str(ROOT / "mcp/studio-mcp.py")], "env": {"STUDIO_URL": "http://127.0.0.1:9"},
            "via": "here", "host": "", "enabled": True}
    check = agents.check_tool(tool)
    assert check["ok"] and check["tools"] == ["studio_state", "list_library", "queue_still", "queue_clip", "queue_voice", "queue_song", "queue_edit",
                                             "queue_long", "queue_sound", "queue_soundtrack", "check_clip", "motion_previs", "cancel_job", "list_projects",
                                             "add_to_timeline", "export_timeline", "job_status", "wait_for_jobs", "list_jobs", "slate_films",
                                             "slate_film", "slate_new_film", "slate_add_scene", "slate_add_shot", "slate_render_shot", "upload_file"]


def test_blender_is_started_when_its_socket_is_closed(fake):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    tools = client.get("/api/agent/tools").json()["tools"]
    tools[0] = {**tools[0], "env": {"BLENDER_HOST": "127.0.0.1", "BLENDER_PORT": str(port)}, "start": [PY, str(fake["tmp"] / "listen.py"), str(port)]}
    post("/api/agent/tools", {"tools": tools})
    started = post("/api/agent/tools/blender/start").json()
    try:
        assert started["listening"] and started["started"] and started["log"] == ".studio-agents/blender.log"
        again = post("/api/agent/tools/blender/start").json()
        assert again["listening"] and again["started"] is False
    finally:
        os.killpg(started["pid"], signal.SIGTERM)


def test_a_task_runs_saves_its_files_and_lists_them(fake):
    post("/api/agent/tools", fake_tool(fake))
    upload = agents.HOME / "uploads/plate.png"
    upload.write_bytes(b"plate")
    made = post("/api/agent/tasks", {"prompt": "Build a set", "backend": "claude", "tools": ["fake"], "attach": ["uploads/plate.png"]}).json()
    assert made["status"] in ("queued", "running") and made["tools"] == ["fake"]
    done = wait_for(made["id"])
    assert done["status"] == "done", done
    assert made["thread"] and done["outputs"] == [f"agent/{made['thread']}/render.png"] and done["reply"] == "Made render.png"
    assert "Building the set." in done["log"] and "→ mcp__fake__make" in done["log"] and "tools: fake" in done["log"]
    folder = agents.HOME / "agent" / made["thread"]  # the thread's own folder, the same one each turn
    # the attachment is a copy in a dot folder of its own (never a link to the take, never a second Library item), named to the agent
    copy = folder / ".inputs/plate.png"
    assert copy.read_bytes() == b"plate" and copy.stat().st_ino != upload.stat().st_ino
    copy.write_bytes(b"changed by the agent")
    assert upload.read_bytes() == b"plate"
    said = next(c for c in fake["calls"]() if c.get("cli") == "claude")["stdin"]
    assert said.startswith("Build a set") and ".inputs/plate.png (in the Library: uploads/plate.png)" in said
    assert ".inputs/plate.png" not in done["outputs"] and not any("plate" in p for p in done["outputs"])
    config = json.loads((folder / "mcp.json").read_text())["mcpServers"]["fake"]
    assert config == {"command": PY, "args": [str(fake["tmp"] / "mcp.py")], "env": {"FAKE_X": "1"}}
    args = next(c for c in fake["calls"]() if c.get("cli") == "claude")["args"]
    assert args[args.index("--allowedTools") + 1] == "mcp__fake" and args[args.index("--tools") + 1] == "Read,Write,Glob"
    assert json.loads(args[args.index("--settings") + 1]) == {"permissions": {"blockReadsOutsideWorkingDirectories": True}}
    assert args[args.index("--permission-mode") + 1] == "acceptEdits" and "Bash" not in " ".join(args)
    assert str(folder) in args[args.index("--append-system-prompt") + 1]
    assert client.get(f"/files/agent/{made['thread']}/render.png").content == b"png"
    assert made["id"] in [t["id"] for t in client.get("/api/agent/tasks").json()["tasks"]]
    # the record outlives the app
    record = json.loads((agents.HOME / "agent" / made["id"] / "task.json").read_text())
    assert record["status"] == "done" and record["outputs"] == done["outputs"]
    agents.tasks.clear()
    agents.load_tasks()
    assert client.get(f"/api/agent/tasks/{made['id']}").json()["status"] == "done"


def test_a_codex_task_gets_its_tools_as_config(fake):
    post("/api/agent/tools", fake_tool(fake))
    made = post("/api/agent/tasks", {"prompt": "Lay out the scene", "backend": "codex", "tools": ["fake"]}).json()
    done = wait_for(made["id"])
    assert done["status"] == "done" and done["reply"] == "ready" and done["outputs"] == [f"agent/{made['thread']}/set.json"]
    assert "→ fake.make" in done["log"]
    call = [c for c in fake["calls"]() if c.get("cli") == "codex"][-1]
    args = call["args"]
    assert call["stdin"].startswith("You are working for a filmmaker inside Imagination Studio.") and call["stdin"].endswith("Lay out the scene")
    assert args[:6] == ["exec", "--skip-git-repo-check", "--sandbox", "workspace-write", "--json", "-c"]
    assert f'mcp_servers.fake.command={json.dumps(PY)}' in args and 'mcp_servers.fake.env={FAKE_X = "1"}' in args
    # codex exec never asks: without this a Studio tool call is refused ("approval policy is never")
    assert args[args.index('mcp_servers.fake.default_tools_approval_mode="approve"') - 1] == "-c"
    assert sum("default_tools_approval_mode" in a for a in args) == 1  # one per tool server, and only the chosen ones


def test_a_task_over_ssh_brings_its_files_back(fake):
    post("/api/agent/tools", fake_tool(fake, via="ssh", host="user@workstation"))
    post("/api/brains/settings", {"remote": {"host": "user@workstation", "backends": ["claude"]}})
    made = post("/api/agent/tasks", {"prompt": "Build a set", "backend": "claude", "tools": ["fake"]}).json()
    done = wait_for(made["id"])
    assert done["status"] == "done", done
    assert done["outputs"] == [f"agent/{made['thread']}/render.png"]
    call = next(c for c in fake["calls"]() if c.get("cli") == "claude")
    assert ".slate-studio-agent" in call["cwd"] and str(fake["tmp"] / "remote-home") in call["cwd"]
    # the tool runs on the harness's own host, so it is started there directly
    config = json.loads((Path(call["cwd"]) / "mcp.json").read_text())["mcpServers"]["fake"]
    assert config["command"] == PY
    # a tool on this machine needs the way back to it
    post("/api/agent/tools", fake_tool(fake))
    failed = wait_for(post("/api/agent/tasks", {"prompt": "x", "backend": "claude", "tools": ["fake"]}).json()["id"])
    assert failed["status"] == "failed" and "studio_host" in failed["error"]
    post("/api/brains/settings", {"remote": {"studio_host": "user@studio-box"}})
    agents.save_tools({"tools": [fake_tool(fake)]})
    wrapped = agents.mcp_servers({"tools": ["fake"]}, "user@workstation", agents.saved_settings())["fake"]
    assert wrapped["command"] == "ssh" and "user@studio-box" in wrapped["args"] and "FAKE_X=1" in wrapped["args"][-1]


def test_a_task_is_cancelled(fake, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "30")
    made = post("/api/agent/tasks", {"prompt": "Take your time", "backend": "claude", "tools": []}).json()
    wait_for(made["id"], ("running",))
    time.sleep(0.5)
    started = time.time()
    assert post(f"/api/agent/tasks/{made['id']}/cancel").json()["status"] == "cancelled"
    done = wait_for(made["id"], ("cancelled",))
    assert time.time() - started < 10 and done["outputs"] == []
    time.sleep(0.3)
    assert agents.running["id"] is None
    # and one still waiting never runs
    monkeypatch.setenv("FAKE_SLEEP", "3")
    first = post("/api/agent/tasks", {"prompt": "one", "backend": "claude", "tools": []}).json()
    second = post("/api/agent/tasks", {"prompt": "two", "backend": "claude", "tools": []}).json()
    assert client.get(f"/api/agent/tasks/{second['id']}").json()["status"] == "queued"  # one at a time
    assert post(f"/api/agent/tasks/{second['id']}/cancel").json()["status"] == "cancelled"
    post(f"/api/agent/tasks/{first['id']}/cancel")
    assert post("/api/agent/tasks/none/cancel").status_code == 404


def test_tasks_are_checked(fake):
    assert post("/api/agent/tasks", {"prompt": "", "backend": "claude"}).status_code == 400
    assert post("/api/agent/tasks", {"prompt": "x", "backend": "nonsense"}).status_code == 400
    assert post("/api/agent/tasks", {"prompt": "x", "backend": "claude", "tools": [], "thread": "th-0000-000000-000000"}).status_code == 404
    assert post("/api/agent/tasks", {"prompt": "x", "backend": "claude", "tools": ["nope"]}).status_code == 400
    assert post("/api/agent/tasks", {"prompt": "x", "backend": "claude", "tools": [], "attach": ["../../etc/passwd"]}).status_code == 400
    agents.save_tools({"tools": agents.default_tools()})  # the Studio's own records are never attachments
    for inside in (".studio-agents/tools.json", "settings.json"):
        (agents.HOME / inside).write_text("{}")
    assert post("/api/agent/tasks", {"prompt": "x", "backend": "claude", "tools": [], "attach": [".studio-agents/tools.json"]}).status_code == 400


def test_the_time_limit_runs_while_the_prompt_is_written(fake, monkeypatch):
    # a CLI that never reads its stdin, with a prompt larger than a pipe holds: the write must not hang past the limit
    monkeypatch.setattr(agents, "TASK_TIMEOUT", 1)
    task = {"id": "0101-000000-feed00", "backend": "claude", "status": "running", "_host": ""}
    agents.task_folder(task["id"]).mkdir(parents=True, exist_ok=True)
    started = time.time()
    with open(agents.task_folder(task["id"]) / "log.txt", "w") as log:
        code = agents.stream(task, [PY, "-c", "import time; time.sleep(60)"], None, "x" * (1 << 20), log)
    assert time.time() - started < 10 and code is not None and "stopped after" in task["_error"]
    agents.running.update(id=None, process=None)


def test_a_streamed_message_is_one_line_of_the_log():
    task = {"id": "t", "backend": "hermes", "_host": "", "steps": []}
    shown = [agents.render("hermes", json.dumps({"type": "text", "text": word}), task) for word in ("A ", "lighthouse ", "at dusk.")]
    assert shown == ["", "", ""]
    assert agents.render("hermes", json.dumps({"type": "result", "text": "A lighthouse at dusk."}), task) == "A lighthouse at dusk.\n"


def test_only_this_computer_may_use_them(fake):
    assert client.get("/api/brains", headers={"host": "studio.example.com"}).status_code == 403
    assert client.get("/api/agent/tasks", headers={"host": "192.168.1.20:7870"}).status_code == 403
    assert client.get("/api/agent/tools", headers={"host": "spark.tail1234.ts.net"}).status_code == 200
    assert post("/api/agent/tasks", {"prompt": "x"}, headers={"origin": "http://evil.example"}).status_code == 403
    assert post("/api/brains/test", {"backend": "claude"}, headers={"sec-fetch-site": "cross-site"}).status_code == 403
    # a form post is refused before anything runs: by the body check (422) where a JSON body is read, else by the policy
    assert client.post("/api/brains/settings", content="default=codex", headers={"content-type": "application/x-www-form-urlencoded"}).status_code in (415, 422)
    assert client.post("/api/agent/tasks/none/cancel", content="x", headers={"content-type": "text/plain"}).status_code == 415
    assert client.post("/api/agent/tools/fake/check").status_code == 415
    away = TestClient(server.app, client=("10.0.0.5", 50000))
    assert away.get("/api/brains").status_code == 403 and away.post("/api/agent/tasks", json={"prompt": "x"}).status_code == 403


def test_the_update_check_does_not_wait_on_agents(fake, monkeypatch):
    monkeypatch.setenv("STUDIO_GPU_LOCK", str(fake["tmp"] / "no-lock"))
    assert server.update_busy() is False
