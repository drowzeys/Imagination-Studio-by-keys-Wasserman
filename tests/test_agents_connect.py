"""Connecting a brain from the Studio (app/agents.py, the connect section): python3 -m pytest tests/test_agents_connect.py.
Nothing real runs: claude, npm and ssh are small stand-ins. The stand-in ssh runs the remote line in bash with a home of
its own ("the other computer"), so the Keychain case, the token kept there and the installer over ssh run end to end."""
import json
import os
import stat
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
TOKEN = "sk-ant-oat01-" + "Q" * 30 + "_x-" + "7" * 12

CLAUDE = r'''
import json, os, sys, time
args = sys.argv[1:]
token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
if args == ["--version"]:
    print("9.9.9 (Claude Code)"); sys.exit(0)
if args[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": bool(token) or os.environ.get("FAKE_SIGNED") == "1", "authMethod": "oauth_token" if token else "claude.ai"})); sys.exit(0)
if args[:2] == ["auth", "logout"]:
    print("Logged out."); sys.exit(0)
if args == ["setup-token"]:
    print("Opening browser to sign in")
    print("If the browser did not open, visit: https://claude.com/cai/oauth/authorize?code=true&client_id=abc&state=xyz")
    sys.stdout.write("Paste code here if prompted > "); sys.stdout.flush()
    code = sys.stdin.readline().strip()
    if code != "good#code":
        print("OAuth error: invalid code"); sys.exit(1)
    print("\nYour OAuth token (valid for 1 year):\n\n" + os.environ.get("FAKE_TOKEN", "") + "\n\nStore this token securely.")
    sys.exit(0)
if "-p" in args:
    sys.stdin.read()
    if os.environ.get("FAKE_SLOW"):
        time.sleep(5)
    if not (token or os.environ.get("FAKE_SIGNED") == "1"):
        print(json.dumps({"is_error": True, "result": "Invalid API key · Please run /login"})); sys.exit(1)
    print(json.dumps({"is_error": False, "result": "ok"})); sys.exit(0)
sys.exit(3)
'''
# npm: `npm prefix -g` names a folder (writable or not, by FAKE_NPM_PREFIX), an install writes a claude stand-in beside it
NPM = r'''
import os, sys
args = sys.argv[1:]
if args[:2] == ["prefix", "-g"]:
    print(os.environ["FAKE_NPM_PREFIX"]); sys.exit(0)
if args[:2] == ["install", "-g"]:
    print("added 1 package in 2s")
    target = os.environ["FAKE_NPM_BIN"]
    if "--prefix" in args:
        target = os.path.join(args[args.index("--prefix") + 1], "bin"); os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, "claude"), "w") as f:
        f.write(open(os.environ["FAKE_CLAUDE_SOURCE"]).read())
    os.chmod(os.path.join(target, "claude"), 0o755)
    sys.exit(0)
sys.exit(3)
'''
# ssh: options up to "--", then the remote line, run with bash in the other computer's home; FAKE_SSH says how it greets
SSH = r'''
import os, subprocess, sys
args = sys.argv[1:]
mode = os.environ.get("FAKE_SSH", "ok")
trusting = "StrictHostKeyChecking=accept-new" in args
if mode == "unknown_host" and not trusting:
    sys.stderr.write("Host key verification failed.\r\n"); sys.exit(255)
if mode == "denied":
    sys.stderr.write("me@mac: Permission denied (publickey,password).\r\n"); sys.exit(255)
line = " ".join(args[args.index("--") + 1:])
remote = os.environ["FAKE_REMOTE"]
for real in ("/opt/homebrew/bin", "/usr/local/bin"):  # the machine running the tests is not the other computer
    line = line.replace(real, remote + "/none")
env = {"HOME": remote, "PATH": remote + "/sys:/usr/bin:/bin", "FAKE_TOKEN": os.environ.get("FAKE_TOKEN", ""), "TERM": "dumb"}
sys.exit(subprocess.run(["bash", "-c", line], env=env, cwd=remote).returncode)
'''
UNAME = "#!/bin/sh\necho Darwin\n"
SECURITY = '#!/bin/sh\n[ -f "$HOME/.keychain-has-claude" ] && exit 0\nexit 44\n'


def script(folder: Path, name: str, body: str, interpreter: str = PY) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(body if body.startswith("#!") else f"#!{interpreter}\n{body}")
    path.chmod(0o755)
    return path


@pytest.fixture
def fake(tmp_path, monkeypatch):
    bin_, remote, home = tmp_path / "bin", tmp_path / "mac", tmp_path / "home"
    home.mkdir()
    source = script(tmp_path / "src", "claude", CLAUDE)
    script(bin_, "ssh", SSH)
    script(remote / ".local/bin", "claude", CLAUDE)
    script(remote / "sys", "uname", UNAME)
    script(remote / "sys", "security", SECURITY)
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}/usr/bin{os.pathsep}/bin")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("FAKE_REMOTE", str(remote))
    monkeypatch.setenv("FAKE_TOKEN", TOKEN)
    monkeypatch.setenv("FAKE_CLAUDE_SOURCE", str(source))
    monkeypatch.delenv("FAKE_SSH", raising=False)
    monkeypatch.delenv("FAKE_SIGNED", raising=False)
    monkeypatch.setattr(slate, "CLI_DIRS", ())
    monkeypatch.setattr(slate, "find_local", lambda given="": (None, []))
    monkeypatch.setattr(agents, "MAC_CODEX", ())
    monkeypatch.setattr(agents, "fingerprint", lambda host: "SHA256:fakeprint (ED25519)")
    (agents.HOME / ".studio-agents" / "settings.json").unlink(missing_ok=True)
    for cache in (agents.probes, agents.statuses, agents.verified, agents.machines, agents.codex_checks):
        cache.clear()
    agents.installing.clear()
    agents.installing["backend"] = None
    yield {"bin": bin_, "remote": remote, "home": home, "tmp": tmp_path}
    agents.cancel_signin(agents.signin.get("backend") or "claude")
    agents.signin.clear()
    agents.signin["backend"] = None


def post(path, body=None, **kw):
    return client.post(path, json=body if body is not None else {}, **kw)


def until(path, key, states, seconds=10):
    ends = time.time() + seconds
    while time.time() < ends:
        now = client.get(path).json()
        if now[key] in states:
            return now
        time.sleep(0.1)
    raise AssertionError(f"{path} never reached {states}: {now}")


def remote_mac(backends=("claude",)):
    assert post("/api/brains/settings", {"remote": {"host": "me@mac", "backends": list(backends)}}).status_code == 200


# ---------- a. find it, or install it ----------

def test_a_missing_cli_is_installed_with_npm_here_and_then_found(fake, monkeypatch):
    prefix = fake["tmp"] / "npm-prefix"
    prefix.mkdir()
    monkeypatch.setenv("FAKE_NPM_PREFIX", str(prefix))
    monkeypatch.setenv("FAKE_NPM_BIN", str(fake["bin"]))
    script(fake["bin"], "npm", NPM)
    card = client.get("/api/brains/claude/connect").json()
    assert card["find"]["found"] is False and card["signin"]["state"] == "missing"
    plan = client.get("/api/brains/claude/install?plan=1").json()["plan"]
    assert plan["ok"] and plan["command"] == "npm install -g @anthropic-ai/claude-code@latest" and plan["by"] == "npm" and plan["where"] == "here"
    started = post("/api/brains/claude/install")
    assert started.status_code == 200, started.text
    done = until("/api/brains/claude/install", "state", ("done", "failed"))
    assert done["state"] == "done" and "added 1 package in 2s" in done["lines"]
    assert client.get("/api/brains/claude/connect").json()["find"]["found"] is True


def test_npm_whose_prefix_is_not_the_users_installs_into_their_home_and_nothing_uses_sudo(fake, monkeypatch):
    locked = fake["tmp"] / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    monkeypatch.setenv("FAKE_NPM_PREFIX", str(locked))
    monkeypatch.setenv("FAKE_NPM_BIN", str(fake["bin"]))
    script(fake["bin"], "npm", NPM)
    plan = agents.install_plan("claude")
    assert plan["command"] == 'npm install -g --prefix "$HOME/.npm-global" @anthropic-ai/claude-code@latest'
    post("/api/brains/claude/install")
    assert until("/api/brains/claude/install", "state", ("done", "failed"))["state"] == "done"
    assert (fake["home"] / ".npm-global/bin/claude").is_file()
    assert client.get("/api/brains/claude/connect").json()["find"]["found"] is True  # ~/.npm-global/bin is looked in
    assert not any("sudo" in line for recipes in agents.INSTALLERS.values() for _, line, _ in recipes)
    locked.chmod(0o755)


def test_no_tool_to_install_with_says_what_is_missing_and_install_is_refused(fake, monkeypatch):
    monkeypatch.setitem(agents.INSTALLERS, "grok", [("brew-none", "brew-none install grok", "nothing")])
    plan = agents.install_plan("grok")
    assert plan["ok"] is False and "brew-none" in plan["reason"]
    assert post("/api/brains/grok/install").status_code == 400


def test_the_installer_runs_on_the_other_computer_over_ssh(fake, monkeypatch):
    remote_mac(["claude"])
    (fake["remote"] / ".local/bin/claude").unlink()
    prefix = fake["remote"] / "npm"
    script(fake["remote"] / "sys", "npm", NPM)
    monkeypatch.setenv("FAKE_NPM_PREFIX", str(prefix))
    (fake["remote"] / "sys" / "npm").write_text("#!/bin/sh\n" + f'FAKE_NPM_PREFIX={prefix} FAKE_NPM_BIN={fake["remote"]}/.local/bin FAKE_CLAUDE_SOURCE={os.environ["FAKE_CLAUDE_SOURCE"]} exec {PY} {fake["tmp"]}/npm.py "$@"\n')
    script(fake["tmp"], "npm.py", NPM)
    prefix.mkdir()
    card = client.get("/api/brains/claude/connect").json()
    assert card["where"] == "ssh:me@mac" and card["find"]["found"] is False
    assert client.get("/api/brains/claude/install?plan=1").json()["plan"]["where"] == "ssh:me@mac"
    post("/api/brains/claude/install")
    assert until("/api/brains/claude/install", "state", ("done", "failed"))["state"] == "done"
    assert (fake["remote"] / ".local/bin/claude").is_file()
    assert client.get("/api/brains/claude/connect").json()["find"]["found"] is True


# ---------- b. sign in: a Mac over ssh keeps Claude Code's sign-in in its Keychain ----------

def test_a_mac_over_ssh_signed_in_at_its_desktop_says_so_and_allow_over_ssh_keeps_a_token_there(fake):
    remote_mac(["claude"])
    (fake["remote"] / ".keychain-has-claude").write_text("")
    status = client.get("/api/brains/claude/status").json()
    assert status["state"] == "keychain" and "Keychain" in status["detail"]
    assert client.get("/api/brains/claude/connect").json()["token_flow"] is True
    started = post("/api/brains/claude/signin").json()
    assert started["command"] == "claude setup-token"
    now = until("/api/brains/claude/signin", "state", ("needs_code",))
    assert now["url"].startswith("https://claude.com/cai/oauth/authorize") and now["token"] == "waiting"
    post("/api/brains/claude/signin/code", {"code": "good#code"})
    done = until("/api/brains/claude/signin", "state", ("done", "failed"))
    assert done["state"] == "done", done
    assert done["token"] == "saved"
    kept = fake["remote"] / agents.CLAUDE_TOKEN
    assert kept.read_text().strip() == TOKEN
    assert stat.S_IMODE(kept.stat().st_mode) == 0o600 and stat.S_IMODE(kept.parent.stat().st_mode) == 0o700
    # the token went from the pty to that computer only: not in what the page sees, not in the studio's folder
    assert TOKEN not in json.dumps(done) and TOKEN not in agents.signin.get("raw", "")
    assert not any(TOKEN in p.read_text(errors="ignore") for p in agents.HOME.rglob("*") if p.is_file())
    # every remote command now carries it: the status and one real answer
    assert client.get("/api/brains/claude/status").json()["state"] == "signed_in"
    said = post("/api/brains/claude/verify").json()
    assert said["ok"] and said["text"] == "ok"
    # signing out there forgets it
    post("/api/brains/claude/signout")
    assert not kept.exists()


def test_a_token_that_cannot_be_saved_fails_the_sign_in_and_is_not_kept(fake, monkeypatch):
    remote_mac(["claude"])
    monkeypatch.setattr(agents, "save_token", lambda host, token: "The token could not be saved on that Mac: disk full")
    post("/api/brains/claude/signin")
    until("/api/brains/claude/signin", "state", ("needs_code",))
    post("/api/brains/claude/signin/code", {"code": "good#code"})
    failed = until("/api/brains/claude/signin", "state", ("done", "failed"))
    assert failed["state"] == "failed" and "disk full" in failed["reason"] and TOKEN not in json.dumps(failed)


def test_a_linux_computer_over_ssh_uses_the_plain_sign_in(fake):
    remote_mac(["claude"])
    (fake["remote"] / "sys" / "uname").write_text("#!/bin/sh\necho Linux\n")
    assert client.get("/api/brains/claude/status").json()["state"] == "signed_out"  # no Keychain to blame
    assert post("/api/brains/claude/signin").json()["command"] == "claude auth login --claudeai"
    post("/api/brains/claude/signin/cancel")


# ---------- c. one real answer, and the one fix ----------

def test_verify_answers_once_and_says_the_fix_when_it_cannot(fake, monkeypatch):
    script(fake["bin"], "claude", CLAUDE)
    failed = post("/api/brains/claude/verify").json()
    assert failed["ok"] is False and "/login" in failed["error"] and failed["fix"]["action"] == "signin"
    monkeypatch.setenv("FAKE_SIGNED", "1")
    ok = post("/api/brains/claude/verify").json()
    assert ok["ok"] and ok["text"] == "ok" and ok["where"] == "here"
    assert client.get("/api/brains/claude/connect").json()["verify"]["ok"] is True  # kept for the checklist
    monkeypatch.setenv("FAKE_SLOW", "1")
    monkeypatch.setattr(agents, "VERIFY_TIMEOUT", 1)
    slow = post("/api/brains/claude/verify").json()
    assert slow["ok"] is False and "No answer in 1 seconds" in slow["error"] and slow["fix"]["action"] == "verify"
    assert agents.fix_of("codex", "This Codex CLI is older than your ChatGPT account's models", "")["action"] == "install"
    assert agents.fix_of("claude", "me@mac did not answer over ssh in 8 seconds", "me@mac")["action"] == "link"
    assert agents.fix_of("claude", "Claude Code is not installed on this machine", "")["label"] == "Install Claude Code"


# ---------- d. the default ----------

def test_make_default_sets_the_brain_and_the_agent_rooms_choice(fake):
    assert post("/api/brains/codex/default").json()["settings"]["agent"] == "codex"
    settings = post("/api/brains/gemini/default").json()["settings"]
    assert settings["default"] == "gemini" and settings["agent"] == "codex"  # Gemini is a conversation: the room keeps Codex
    assert client.get("/api/brains").json()["settings"]["agent"] == "codex"
    assert post("/api/brains/nobody/default").status_code == 404


def test_a_signed_in_default_keeps_its_place_and_a_signed_out_one_gives_way(fake, monkeypatch):
    script(fake["bin"], "claude", CLAUDE)
    script(fake["bin"], "grok", 'import sys\nprint("You are logged in with grok.com.") if sys.argv[1:] == ["models"] else print("grok 1.0")\n')
    post("/api/brains/settings", {"default": "claude"})
    assert client.get("/api/brains/grok/connect").json()["default_ready"] is False  # Claude Code is signed out: Connect may take its place
    monkeypatch.setenv("FAKE_SIGNED", "1")
    agents.statuses.clear()
    assert client.get("/api/brains/grok/connect").json()["default_ready"] is True  # signed in: it stays, the page offers a button


# ---------- e. linking another computer ----------

def test_link_makes_a_key_once_and_shows_the_line_to_run_there(fake):
    first = client.get("/api/brains/link").json()
    assert first["has_key"] is False and first["command"] is None
    made = post("/api/brains/link/key").json()
    assert made["has_key"] and made["public_key"].startswith("ssh-ed25519 ") and made["public_key"].endswith(" imagination-studio")
    key = fake["home"] / ".ssh/id_ed25519"
    assert stat.S_IMODE(key.stat().st_mode) == 0o600 and "authorized_keys" in made["command"] and made["public_key"] in made["command"]
    before = key.read_bytes()
    again = post("/api/brains/link/key").json()
    assert again["public_key"] == made["public_key"] and key.read_bytes() == before  # never replaced


def test_link_test_meets_trusts_and_saves_the_computer_with_its_agents(fake, monkeypatch):
    monkeypatch.setenv("FAKE_SSH", "denied")
    assert post("/api/brains/link/test", {"host": "me@mac"}).json()["state"] == "denied"
    monkeypatch.setenv("FAKE_SSH", "unknown_host")
    met = post("/api/brains/link/test", {"host": "me@mac"}).json()
    assert met["state"] == "unknown_host" and met["fingerprint"] == "SHA256:fakeprint (ED25519)"
    trusted = post("/api/brains/link/test", {"host": "me@mac", "trust": True}).json()
    assert trusted == {"ok": True, "host": "me@mac", "state": "ok", "os": "Darwin"}
    monkeypatch.setenv("FAKE_SSH", "ok")
    saved = post("/api/brains/link/save", {"host": "me@mac"}).json()
    assert saved["found"] == ["claude"] and saved["settings"]["remote"]["host"] == "me@mac"
    assert client.get("/api/brains/claude/connect").json()["where"] == "ssh:me@mac"
    assert post("/api/brains/link/test", {"host": "me@mac; rm -rf /"}).status_code == 400


def test_the_connect_routes_keep_the_brains_guards(fake):
    assert client.post("/api/brains/claude/install", content=b"{}", headers={"content-type": "text/plain"}).status_code in (415, 422)
    assert post("/api/brains/claude/verify", headers={"origin": "https://elsewhere.example"}).status_code == 403
    assert post("/api/brains/link/key", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert client.get("/api/brains/claude/connect", headers={"host": "192.168.1.9:7870"}).status_code == 403


def test_an_older_codex_on_path_gives_way_to_the_chatgpt_apps_newer_one(fake, monkeypatch):
    # measured on a Mac: Homebrew's codex 0.130 is refused a ChatGPT account's models and spends 68 s saying so; the app's 0.162 answers in 6 s
    script(fake["bin"], "codex", 'import sys\nprint("codex-cli 0.130.0")\n')
    app = script(fake["tmp"] / "ChatGPT.app/Contents/Resources", "codex", 'import sys\nprint("codex-cli 0.162.0-alpha.17.2")\n')
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(agents, "MAC_CODEX", (str(app),))
    monkeypatch.setattr(agents, "codex_broken", lambda path: None)
    agents.versions.clear()
    assert agents.cli("codex") == str(app)
    codex = next(b for b in agents.detect() if b["id"] == "codex")
    assert codex["path"] == str(app) and "newer" in codex["note"] and "shares your ChatGPT sign-in" in codex["note"]
    script(fake["bin"], "codex", 'import sys\nprint("codex-cli 0.170.0")\n')  # a newer one on PATH is kept
    agents.versions.clear()
    assert agents.cli("codex") == str(fake["bin"] / "codex")


def test_over_ssh_the_chatgpt_apps_newer_codex_is_used_too(fake, monkeypatch):
    # measured: from a DGX Spark the Mac's Homebrew codex 0.130 was found first, and that one is refused the account's models
    script(fake["remote"] / ".local/bin", "codex", 'print("codex-cli 0.130.0")\n')
    app = fake["remote"] / "ChatGPT.app/Contents/Resources/codex-cli"
    script(app / "bin", "codex", 'print("codex-cli 0.162.0-alpha.17.2")\n')
    monkeypatch.setattr(agents, "MAC_CODEX", (str(app),))
    remote_mac(["codex"])
    look = agents.probe_remote("me@mac", ["codex"])
    assert look["codex"] == (str(app / "bin/codex"), "codex-cli 0.162.0-alpha.17.2")
    script(fake["remote"] / ".local/bin", "codex", 'print("codex-cli 0.170.0")\n')
    assert agents.probe_remote("me@mac", ["codex"])["codex"][1] == "codex-cli 0.170.0"  # a newer one on PATH is kept
