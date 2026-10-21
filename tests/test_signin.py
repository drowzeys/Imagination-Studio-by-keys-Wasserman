"""Signing a brain in from the Studio (app/agents.py, the signin section): python3 -m pytest tests/test_signin.py.
No real sign-in ever runs: claude, codex and grok are small stand-ins on PATH that print what the real ones print
(an address, a prompt for a code, a device code) and read their terminal."""
import json
import os
import subprocess
import sys
import tempfile
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
args = sys.argv[1:]
if args == ["--version"]:
    print("9.9.9 (Claude Code)"); sys.exit(0)
if args[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": os.environ.get("FAKE_SIGNED") == "1", "authMethod": "claude.ai", "subscriptionType": "max", "email": "someone@example.com"})); sys.exit(0)
if args[:2] == ["auth", "logout"]:
    print("Successfully logged out from your Anthropic account."); sys.exit(0)
if args[:2] == ["auth", "login"]:
    if os.environ.get("FAKE_SIGNIN") == "hang":
        time.sleep(60)
    print("\x1b[1mOpening browser to sign in\x1b[0m")
    print("If the browser did not open, visit: https://claude.ai/oauth/authorize?code=true&client_id=abc&state=Zm9vYmFyMTIzNDU2Nzg5MGFiY2RlZg")
    sys.stdout.write("Paste code here if prompted > "); sys.stdout.flush()
    code = sys.stdin.readline().strip()
    if code == "good#code-123":
        print("Login successful."); sys.exit(0)
    print("OAuth error: invalid code"); sys.exit(1)
sys.exit(3)
'''
CODEX = r'''
import os, sys, time
args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli 9.9.9"); sys.exit(0)
if os.environ.get("FAKE_CODEX_BROKEN") == os.path.realpath(sys.argv[0]):
    sys.stderr.write("Error loading configuration: ~/.codex/config.toml:5:16: unknown variant `priority`, expected `fast` or `flex`\n"); sys.exit(1)
if args == ["login", "status"]:
    print("Logged in using ChatGPT"); sys.exit(0)
if args == ["login", "--device-auth"]:
    print("Welcome to Codex [v9.9.9]")
    print("Follow these steps to sign in with ChatGPT using device code authorization:")
    print("1. Open this link in your browser and sign in to your account")
    print("   \x1b]8;;https://auth.openai.com/codex/device\x07https://auth.openai.com/codex/device\x1b]8;;\x07")
    print("2. Enter this one-time code (expires in 15 minutes)")
    print("   ABCD-12345")
    sys.stdout.flush(); time.sleep(1.2)
    print("Successfully logged in"); sys.exit(0)
if args == ["login"]:
    print("Starting local login server on http://localhost:1455.")
    print("If your browser did not open, navigate to this URL to authenticate:")
    print("")
    print("https://auth.openai.com/oauth/authorize?response_type=code&client_id=app_x&redirect_uri=http%3A%2F%2Flocalhost%3A1455")
    sys.stdout.flush(); time.sleep(1.2)
    print("Successfully logged in"); sys.exit(0)
if args == ["logout"]:
    print("Successfully logged out"); sys.exit(0)
sys.exit(3)
'''
GROK = r'''
import sys
args = sys.argv[1:]
if args == ["--version"]:
    print("grok 9.9.9"); sys.exit(0)
if args == ["models"]:
    print("You are logged in with grok.com.\n\nDefault model: grok-x"); sys.exit(0)
if args == ["login", "--device-auth"]:
    print("error: could not reach auth.x.ai (network unreachable)"); sys.exit(2)
sys.exit(3)
'''
HERMES = r'''
import sys
args = sys.argv[1:]
if args == ["--version"]:
    print("Hermes Agent v9"); sys.exit(0)
if args == ["auth", "list"]:
    print("copilot (0 credentials):\n\nopenai-codex (2 credentials):\n  #1  device_code oauth id=bbb3bb priority=0 ←"); sys.exit(0)
import json, os
config = os.path.join(os.environ.get("FAKE_HERMES_DIR", "/tmp"), "hermes-config.json")
saved = json.load(open(config)) if os.path.exists(config) else {}
if args[:3] == ["auth", "add", "openai-codex"]:
    print("Signed in to openai-codex."); sys.exit(0)
if args[:2] == ["config", "get"]:
    print(saved.get(args[2], "")); sys.exit(0)
if args[:2] == ["config", "set"]:
    saved[args[2]] = args[3]; json.dump(saved, open(config, "w")); sys.exit(0)
sys.exit(3)
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
    for name, body in (("claude", CLAUDE), ("codex", CODEX), ("grok", GROK), ("hermes", HERMES)):
        script(bin_, name, body)
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}/usr/bin{os.pathsep}/bin")
    monkeypatch.delenv("STUDIO_SIGNIN_DEVICE", raising=False)
    monkeypatch.delenv("FAKE_CODEX_BROKEN", raising=False)
    agents.codex_checks.clear()
    monkeypatch.setattr(slate, "CLI_DIRS", ())
    monkeypatch.setattr(slate, "find_local", lambda given="": (None, []))
    monkeypatch.setattr(agents, "MAC_CODEX", ())
    (agents.HOME / ".studio-agents" / "settings.json").unlink(missing_ok=True)
    agents.probes.clear()
    agents.statuses.clear()
    yield {"bin": bin_, "tmp": tmp_path}
    agents.cancel_signin(agents.signin.get("backend") or "claude")
    agents.signin.clear()
    agents.signin["backend"] = None


def post(path, body=None, **kw):
    return client.post(path, json=body if body is not None else {}, **kw)


def until(backend, states, seconds=8):
    ends = time.time() + seconds
    while time.time() < ends:
        now = client.get(f"/api/brains/{backend}/signin").json()
        if now["state"] in states:
            return now
        time.sleep(0.1)
    raise AssertionError(f"{backend} never reached {states}: {now}")


def test_claude_asks_for_the_code_the_page_shows_and_signs_in(fake):
    started = post("/api/brains/claude/signin")
    assert started.status_code == 200, started.text
    now = until("claude", ("needs_code",))
    assert now["url"].startswith("https://claude.ai/oauth/authorize?code=true&client_id=abc")
    assert "Paste code" in now["prompt"]
    # the log shows the address without its query and nothing token-like
    assert not any("Zm9vYmFy" in line or "client_id" in line for line in now["lines"])
    assert any("https://claude.ai/oauth/authorize?…" in line for line in now["lines"])
    assert post("/api/brains/claude/signin").status_code == 409  # one at a time
    assert post("/api/brains/claude/signin/code", {"code": "two\nlines"}).status_code == 400
    assert post("/api/brains/claude/signin/code", {"code": "good#code-123"}).json()["state"] in ("verifying", "done")
    done = until("claude", ("done", "failed"))
    assert done["state"] == "done", done
    assert not any("good#code-123" in line for line in done["lines"])  # the pasted code never comes back


def test_two_sign_ins_at_once_one_is_refused(fake, monkeypatch):
    # the place is taken before the CLI starts: a second start in that gap is refused, not run beside the first
    import threading
    opened, real = [], agents.open_signin

    def slow(*args):
        opened.append(args[0])
        time.sleep(0.5)
        return real(*args)
    monkeypatch.setattr(agents, "open_signin", slow)
    answers = []
    starts = [threading.Thread(target=lambda: answers.append(post("/api/brains/claude/signin").status_code)) for _ in range(2)]
    for start in starts:
        start.start()
    for start in starts:
        start.join(10)
    assert sorted(answers) == [200, 409] and opened == ["claude"]
    post("/api/brains/claude/signin/cancel")
    until("claude", ("failed",))


def test_a_wrong_code_fails_with_the_clis_reason(fake):
    post("/api/brains/claude/signin")
    until("claude", ("needs_code",))
    post("/api/brains/claude/signin/code", {"code": "wrong-code"})
    failed = until("claude", ("failed",))
    assert "invalid code" in failed["reason"] and "exit 1" in failed["reason"]


def test_codex_on_another_computer_gives_a_device_code(fake, monkeypatch):
    monkeypatch.setenv("STUDIO_SIGNIN_DEVICE", "1")
    started = post("/api/brains/codex/signin").json()
    assert started["command"] == "codex login --device-auth"
    now = until("codex", ("device_code",))
    assert now["url"] == "https://auth.openai.com/codex/device" and now["code"] == "ABCD-12345"
    assert not any("ABCD-12345" in line for line in now["lines"])  # the code is in the box, not the log
    assert until("codex", ("done", "failed"))["state"] == "done"


def test_codex_here_opens_the_openai_page_not_the_localhost_callback(fake):
    post("/api/brains/codex/signin")
    now = until("codex", ("needs_browser",))
    assert now["url"].startswith("https://auth.openai.com/oauth/authorize?")
    assert until("codex", ("done", "failed"))["state"] == "done"


def test_a_codex_on_another_computer_always_takes_the_device_code():
    assert agents.recipe_of("codex", True)["args"] == ["login", "--device-auth"]
    assert agents.recipe_of("codex", False)["args"] == ["login"]
    assert agents.recipe_of("grok", False)["args"] == ["login", "--device-auth"]
    assert agents.recipe_of("gemini", False)["flow"] == "terminal"
    with pytest.raises(agents.HTTPException):
        agents.recipe_of("hermes", False, {"provider": "anything; rm -rf"})


def test_a_failure_says_why(fake):
    post("/api/brains/grok/signin")
    failed = until("grok", ("failed",))
    assert "network unreachable" in failed["reason"] and "exit 2" in failed["reason"]


def test_cancel_stops_the_cli(fake, monkeypatch):
    monkeypatch.setenv("FAKE_SIGNIN", "hang")
    post("/api/brains/claude/signin")
    process = agents.signin["process"]
    cancelled = post("/api/brains/claude/signin/cancel").json()
    assert cancelled["state"] == "failed" and cancelled["reason"] == "Cancelled."
    assert process.poll() is not None


def test_a_sign_in_left_waiting_is_stopped_at_the_limit(fake, monkeypatch):
    monkeypatch.setenv("FAKE_SIGNIN", "hang")
    monkeypatch.setattr(agents, "SIGNIN_TIMEOUT", 1)
    post("/api/brains/claude/signin")
    process = agents.signin["process"]
    failed = until("claude", ("failed",), 6)
    assert "No sign-in after" in failed["reason"] and process.poll() is not None


def test_gemini_and_the_local_model_are_not_run(fake):
    assert post("/api/brains/gemini/signin").status_code == 400
    assert post("/api/brains/local/signin").status_code == 404
    assert client.get("/api/brains/nobody/signin").json()["state"] == "idle"


def test_redaction():
    text = ("Visit https://claude.ai/oauth/authorize?code=true&state=abc123 then enter WXYZ-9876 · "
            "token sk-ant-oat01-AbCdEf0123456789AbCdEf0123 for someone@example.com, code pasted: hello-there")
    out = agents.redact(text, ["hello-there"])
    assert "state=abc123" not in out and "https://claude.ai/oauth/authorize?…" in out
    assert "WXYZ-9876" not in out and "AbCdEf0123456789" not in out and "someone@example.com" not in out
    assert "hello-there" not in out and "UTF-8" in agents.redact("UTF-8 text")
    assert agents.redact("Error loading configuration: /home/someone/.codex/config.toml:5:16") == "Error loading configuration: /home/someone/.codex/config.toml:5:16"
    assert agents.redact("eyJhbGciOiJIUzI1NiJ9abcd.eyJzdWIiOiIxMjM0NTY3ODkwIn0x.sig") == "[hidden].[hidden].sig"


def test_status_comes_from_each_cli(fake, monkeypatch):
    assert client.get("/api/brains/claude/status").json()["state"] == "signed_out"
    agents.statuses.clear()
    monkeypatch.setenv("FAKE_SIGNED", "1")
    claude = client.get("/api/brains/claude/status").json()
    assert claude["state"] == "signed_in" and claude["detail"] == "Claude subscription · max"
    assert "example.com" not in json.dumps(claude)
    assert client.get("/api/brains/codex/status").json()["detail"] == "ChatGPT"
    assert client.get("/api/brains/grok/status").json()["detail"] == "grok.com"
    assert client.get("/api/brains/hermes/status").json()["detail"] == "Providers: openai-codex"
    assert client.get("/api/brains/gemini/status").json()["state"] in ("unknown", "signed_in")
    (fake["bin"] / "grok").unlink()
    agents.statuses.clear()
    assert client.get("/api/brains/grok/status").json()["state"] == "missing"


def test_sign_out_runs_the_clis_own_logout(fake):
    out = post("/api/brains/codex/signout").json()
    assert out["ok"] and out["state"] == "signed_in"  # the stand-in still says signed in; what matters is it ran
    assert post("/api/brains/gemini/signout").status_code == 400
    assert post("/api/brains/hermes/signout", {"provider": "nobody"}).status_code == 400


def test_the_chatgpt_apps_codex_is_used_when_none_is_on_path(fake, monkeypatch, tmp_path):
    (fake["bin"] / "codex").unlink()
    base = tmp_path / "ChatGPT.app" / "Contents" / "Resources" / "codex-cli"
    (base / "bin").mkdir(parents=True)
    (base / "codex-package.json").write_text(json.dumps({"layoutVersion": 1, "entrypoint": "bin/codex"}))
    script(base / "bin", "codex", CODEX)
    monkeypatch.setattr(agents, "MAC_CODEX", (str(base),))
    monkeypatch.setattr(agents.sys, "platform", "darwin")
    assert agents.cli("codex") == str(base / "bin" / "codex")
    codex = next(b for b in client.get("/api/brains").json()["backends"] if b["id"] == "codex")
    assert codex["found"] and codex["label"] == "Codex (from the ChatGPT app)" and codex["signin_flow"] == "pty"
    # an entrypoint that leaves the folder is not followed; bin/codex still is
    (base / "codex-package.json").write_text(json.dumps({"entrypoint": "../../../../evil"}))
    assert agents.bundled_codex(str(base)) == str(base / "bin" / "codex")
    (base / "bin" / "codex").unlink()
    assert agents.bundled_codex(str(base)) is None


def test_a_codex_that_cannot_start_gives_way_to_the_chatgpt_apps(fake, monkeypatch, tmp_path):
    broken = str((fake["bin"] / "codex").resolve())
    monkeypatch.setenv("FAKE_CODEX_BROKEN", broken)
    monkeypatch.setattr(agents.sys, "platform", "darwin")
    # no ChatGPT app: the status line is the CLI's own words, with what to do
    status = client.get("/api/brains/codex/status").json()
    assert status["state"] == "broken" and status["detail"].startswith("Error loading configuration: ~/.codex/config.toml:5:16: unknown variant `priority`")
    assert "brew upgrade codex" in status["hint"]
    # with the ChatGPT app's newer codex beside it, that one is used, and the card says why
    base = tmp_path / "ChatGPT.app" / "Contents" / "Resources" / "codex-cli"
    (base / "bin").mkdir(parents=True)
    script(base / "bin", "codex", CODEX)
    monkeypatch.setattr(agents, "MAC_CODEX", (str(base),))
    agents.statuses.clear()
    assert agents.cli("codex") == str(base / "bin" / "codex")
    codex = next(b for b in client.get("/api/brains").json()["backends"] if b["id"] == "codex")
    assert codex["label"] == "Codex (from the ChatGPT app)" and "cannot start" in codex["note"] and "unknown variant" in codex["note"]
    assert client.get("/api/brains/codex/status").json()["detail"] == "ChatGPT"
    # a codex on PATH that starts is kept
    monkeypatch.delenv("FAKE_CODEX_BROKEN")
    agents.codex_checks.clear()
    assert agents.cli("codex") == str(fake["bin"] / "codex")


def test_a_remote_sign_in_runs_over_ssh_with_a_terminal(monkeypatch):
    monkeypatch.setattr(agents, "remote_cli", lambda host, name: "/opt/homebrew/bin/" + name)
    argv = agents.signin_argv("codex", "me@mac:2222", ["login", "--device-auth"])
    assert argv[:len(agents.SSH)] == agents.SSH and "-tt" in argv and argv[argv.index("-p") + 1] == "2222"
    assert argv[-2] == "--" and argv[-1].endswith("/opt/homebrew/bin/codex login --device-auth")


def test_only_this_computer_and_the_studio_page_may_sign_in(fake):
    assert client.get("/api/brains/claude/status", headers={"host": "studio.example.com"}).status_code == 403
    assert post("/api/brains/claude/signin", headers={"origin": "http://evil.example"}).status_code == 403
    assert post("/api/brains/claude/signin", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert client.post("/api/brains/claude/signin/code", content="code=x", headers={"content-type": "text/plain"}).status_code in (415, 422)
    away = TestClient(server.app, client=("10.0.0.5", 50000))
    assert away.post("/api/brains/claude/signin", json={}).status_code == 403
    assert away.get("/api/brains/claude/signin").status_code == 403
    assert agents.signin.get("process") is None  # nothing was started by any of them


def test_a_hermes_sign_in_also_points_hermes_at_that_provider_and_a_model_it_lists(fake, monkeypatch):
    """`hermes auth add` only stores a credential: with no model in its config Hermes sends an empty one. After the
    sign-in the Studio sets the provider and the first model Hermes' own catalog lists for it."""

    monkeypatch.setenv("FAKE_HERMES_DIR", str(fake["tmp"]))
    asked = []
    monkeypatch.setattr(agents, "hermes_model_for", lambda provider, host: asked.append(provider) or "gpt-test-1")
    assert post("/api/brains/hermes/signin", {"provider": "openai-codex"}).status_code == 200
    assert until("hermes", ("done", "failed"))["state"] == "done"
    saved = json.loads((fake["tmp"] / "hermes-config.json").read_text())
    assert saved == {"model.provider": "openai-codex", "model.default": "gpt-test-1"} and asked == ["openai-codex"]
    # a model already chosen for that provider is kept
    monkeypatch.setattr(agents, "hermes_model_for", lambda provider, host: "gpt-other")
    assert agents.hermes_use("openai-codex", "") == "" and json.loads((fake["tmp"] / "hermes-config.json").read_text())["model.default"] == "gpt-test-1"


def test_a_hermes_sign_in_says_so_when_hermes_lists_no_model(fake, monkeypatch):
    monkeypatch.setenv("FAKE_HERMES_DIR", str(fake["tmp"]))
    monkeypatch.setattr(agents, "hermes_model_for", lambda provider, host: "")
    post("/api/brains/hermes/signin", {"provider": "openai-codex"})
    said = until("hermes", ("done", "failed"))
    assert said["state"] == "failed" and "hermes model" in said["reason"]


def test_an_error_hermes_prints_as_its_answer_is_an_error(fake, monkeypatch):
    for printed in ("Codex Responses request 'model' must be a non-empty string.",
                    'HTTP 400: {"detail":"The \'gpt-5.5\' model is not supported when using Codex with a ChatGPT account."}'):
        monkeypatch.setattr(agents, "run_cli", lambda *a, **k: subprocess.CompletedProcess(a, 0, printed + "\n", ""))
        with pytest.raises(RuntimeError):
            agents.ask("hermes", "", "Reply with the single word: ready")
    monkeypatch.setattr(agents, "run_cli", lambda *a, **k: subprocess.CompletedProcess(a, 0, "ready\n", ""))
    assert agents.ask("hermes", "", "Reply with the single word: ready") == "ready"
