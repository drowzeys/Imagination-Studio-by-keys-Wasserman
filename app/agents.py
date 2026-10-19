"""Brains and agents: the user's own subscriptions wired into the Studio.

A brain is a coding CLI the user is already signed in to (Claude Code, Codex with a ChatGPT sign-in, Gemini CLI,
Grok CLI) or a local OpenAI-compatible server. No API keys: each CLI uses its own sign-in, in print mode. A CLI may
run here or on another machine over ssh (a DGX Spark often has none installed, the Mac beside it does): the prompt
goes on stdin, the arguments are quoted for the remote shell, and nothing is left behind there.

"Ask your agent" runs Claude Code or Codex once, non-interactively, with MCP tools (Blender, Blockout, this Studio)
in a working folder of its own, $STUDIO_HOME/agent/<id>, one task at a time. What it saves there is listed as the
task's outputs and served by the Studio's /files route.

The Agent room keeps conversations: a thread ($STUDIO_HOME/.studio-agents/threads/<id>.json) is a list of turns
{role: user|agent|tool, text, attachments, at}, and each user turn is one agent task run in the thread's own working
folder, $STUDIO_HOME/agent/<thread id>. The next turn continues the harness's own session: Claude Code with
`--session-id` then `--resume`, Codex with `codex exec resume <thread id>`, Hermes with `chat --continue <name>` then
`--resume <session id>`. Gemini, Grok and a local model have no session the Studio can name: they get the thread's
recent turns ahead of the message. Only the session's id is kept, never a secret. An agent that ends its answer on a
question (or the `[question]` marker its instructions ask for) is waiting on the user: the page shows a question card.

Hermes (Nous Research's Hermes Agent CLI) takes its MCP servers only from its own config.yaml (`hermes mcp add`), not
from a file the Studio hands it per task, so in the room it is a conversation: it plans and answers but does not drive
the Studio's tools. Its profile is `hermes_profile` in the Studio's settings.json (Settings → Connections).

Where a tool runs. A tool is a stdio MCP server: `command` (with `env`) run on this machine (`via` "here") or on
`host` over ssh (`via` "ssh"). A harness that runs on this machine starts each tool that way. A harness that runs on
another machine (remote) needs every command to be valid there, so a tool is resolved relative to the harness host:
  1. the tool's own `remote_command`, when set, is used as it is;
  2. a tool `via` ssh on the harness's own host runs there directly (Blockout on the Mac, harness on the Mac);
  3. a tool `via` ssh on a third host stays an ssh command, run from the harness host;
  4. a tool `via` here becomes `ssh <studio_host> -- env K=V command`, where `studio_host` (in the brain settings'
     `remote`) is how the harness host reaches this machine; without it the task is refused with the reason.
"""
from __future__ import annotations

import atexit
import concurrent.futures
import json
import os
import queue
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request

import slate  # the brain's local server lookup and CLI folders, beside this file

HOME: Path | None = None
PACK = Path(__file__).resolve().parent.parent
TEST_TIMEOUT = 180
PROBE_TIMEOUT = 8  # an ssh look at another machine
PROBE_TTL = 60
TASK_TIMEOUT = 2 * 60 * 60  # an agent task is stopped after two hours
CHECK_TIMEOUT = 20  # an MCP server's handshake
LOG_LIMIT = 20000
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
# where coding CLIs install themselves on a remote machine whose non-interactive shell does not have them on PATH
REMOTE_PATH = 'PATH="$PATH:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$HOME/.claude/local:$HOME/.npm-global/bin:$HOME/.bun/bin"; export PATH; '
# a Codex CLI a Mac app carries, used when no codex is on PATH. The ChatGPT desktop app's is a folder, Resources/codex-cli,
# whose codex-package.json names the entrypoint (bin/codex); its sign-in is the ChatGPT app's (as in Sam Wasserman's Slate)
MAC_CODEX = ("/Applications/ChatGPT.app/Contents/Resources/codex-cli", "/Applications/ChatGPT.app/Contents/Resources/codex",
             "/Applications/Codex.app/Contents/Resources/codex")
HOST = re.compile(r"(?:[A-Za-z0-9._-]{1,64}@)?[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252})(?::[0-9]{1,5})?")
TOOL_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
ENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")

# id -> (label, command, how to sign in)
CLIS = {
    "claude": ("Claude Code", "claude", "Run `claude auth login` in a terminal on that machine (your Claude subscription)."),
    "codex": ("Codex · ChatGPT sign-in", "codex", "Run `codex login` in a terminal on that machine and choose Sign in with ChatGPT."),
    "gemini": ("Gemini CLI", "gemini", "Run `gemini` once in a terminal on that machine and sign in with Google."),
    "grok": ("Grok CLI", "grok", "Run `grok login` in a terminal on that machine."),
    "hermes": ("Hermes", "hermes", "Run `hermes setup` in a terminal on that machine to sign in to a provider (`hermes model` picks it)."),
}
BACKENDS = (*CLIS, "local")
HARNESSES = ("claude", "codex")  # run with the Studio's MCP tools; every other brain is a conversation in the Agent room
SESSIONS = ("claude", "codex", "hermes")  # keep a session of their own from one turn to the next
# what a CLI says when its sign-in or install is the trouble, and what to tell the user
HINTS = {
    "claude": [(r"authenticat|oauth|401|logged? ?in|revoked|/login", "Claude Code's sign-in has expired or was never made {where}: run `claude auth login` in a terminal there, then retry.")],
    "codex": [(r"not supported when using Codex with a ChatGPT account|Error loading config|unknown variant",
               "This Codex CLI {where} is older than your ChatGPT account's models or settings: update it (`npm install -g @openai/codex` or `brew upgrade codex`), then retry."),
              (r"401|invalid_grant|not logged in|login|unauthori[sz]ed", "Codex's ChatGPT sign-in has expired {where}: run `codex login` in a terminal there, then retry.")],
    "gemini": [(r"GOOGLE_CLOUD_PROJECT", "This Google account is a Workspace one {where}: set GOOGLE_CLOUD_PROJECT for the Studio, or sign in to Gemini CLI with a personal account."),
               (r"authenticat|oauth|401|login|credentials", "Gemini CLI is not signed in {where}: run `gemini` in a terminal there and sign in, then retry.")],
    "grok": [(r"402|balance exhausted|payment required", "Grok's usage balance is used up {where}: top it up or pick another brain."),
             (r"401|unauthori[sz]ed|not logged in|login", "Grok CLI is not signed in {where}: run `grok login` in a terminal there, then retry.")],
    "hermes": [(r"no (?:inference )?provider|no model (?:is )?configured|not logged in|401|unauthori[sz]ed|api key|hermes setup",
                "Hermes has no provider signed in {where}: run `hermes setup` in a terminal there, then retry.")],
}
SYSTEM = ("You are working for a filmmaker inside Imagination Studio. Save every deliverable (renders as .png, .jpg or .mp4, "
          "models as .glb or .blend, data as .json) into the current folder{folder}. When a tool saves a file itself, give it "
          "that folder's absolute path.{studio} Finish with a short plain summary that lists the files you made.")

# what the Agent room adds: a conversation that may need the user, and the Studio's own tools by name
STUDIO_TOOLS = ("studio_state (call first), list_library, queue_still, queue_clip, queue_voice, queue_song, job_status, "
                "list_jobs, list_projects, add_to_timeline, export_timeline and upload_file")
ROOM = (" This is a conversation with the filmmaker in the Studio's Agent room, and it continues over several turns."
        "{tools} When anything is unclear or a choice matters (length, look, voice, which take), ask the filmmaker one "
        "clear question and stop: write [question] on its own line, then the question, then up to four short choices "
        "as lines starting with '- ' where there are obvious ones. Otherwise do the work, and finish with a short plain "
        "summary that lists the files you made by their Library paths.")
ROOM_TOOLS = (" The Studio's tools ({names}) make stills, clips with sound, voice lines and songs on this machine's GPU, "
              "one job at a time: queue what is asked for, follow each job with job_status until it is done, and name its "
              "outputs.")
CHAT = ("You are the filmmaker's assistant inside Imagination Studio, a local AI film studio (stills, clips with sound, "
        "voice lines, songs, a timeline). In this conversation you cannot run the Studio's tools: help plan (prompts, shot "
        "lists, lines, lyrics), and when the filmmaker wants something made, give the exact prompt to paste into the "
        "Studio, or say that Claude Code or Codex can make it here with the Studio's tools.")
NO_STUDIO = (" The Studio's own tools are not connected in this conversation: for a still, a clip, a voice line or a song, "
             "give the exact prompt to paste into the Studio, or say that Claude Code or Codex can make it here with them.")
QUESTION = re.compile(r"\[question\]", re.I)
LOST_SESSION = re.compile(r"no conversation found|no session found|session[^\n]{0,40}not found|could not find[^\n]{0,20}session|"
                          r"thread[^\n]{0,40}not found|no rollout found", re.I)
THREAD_ID = re.compile(r"th-[0-9]{4}-[0-9]{6}-[0-9a-f]{6}")
PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
HISTORY_CHARS = 8000  # the recent turns a brain without a session of its own reads ahead of the message
STEP_LIMIT = 200
TEXT_LIMIT = 20000

lock = threading.RLock()
probes: dict = {}  # (host or "", cli) -> (monotonic time, found)
tasks: dict[str, dict] = {}
threads: dict[str, dict] = {}
pending: "queue.Queue[str]" = queue.Queue()
running: dict = {"id": None, "process": None, "remote": None}
worker: dict = {"thread": None}


# ---------- settings ----------

def folder() -> Path | None:
    return HOME / ".studio-agents" if HOME else None


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    os.chmod(temp, 0o600)
    temp.replace(path)


def read_json(path: Path | None, fallback):
    try:
        return json.loads(path.read_text()) if path else fallback
    except (OSError, ValueError):
        return fallback


def environment_defaults() -> dict:
    """What the studio's environment says about the brain, for an install with nothing saved yet:
    ``STUDIO_DEFAULT_BRAIN`` (one of BACKENDS), ``STUDIO_LOCAL_ENDPOINT``, ``STUDIO_LOCAL_MODEL``."""

    brain = os.environ.get("STUDIO_DEFAULT_BRAIN", "").strip()
    return {"default": brain if brain in BACKENDS else "claude",
            "local_endpoint": os.environ.get("STUDIO_LOCAL_ENDPOINT", "").strip(),
            "local_model": os.environ.get("STUDIO_LOCAL_MODEL", "").strip()}


def saved_settings() -> dict:
    """The saved settings; a setting never saved takes the environment's value (``environment_defaults``)."""

    saved = read_json(folder() / "settings.json" if folder() else None, {})
    remote = saved.get("remote") if isinstance(saved.get("remote"), dict) else {}
    env = environment_defaults()
    return {"default": saved["default"] if saved.get("default") in BACKENDS else env["default"],
            "remote": {"host": remote.get("host") or "", "backends": [b for b in remote.get("backends") or [] if b in CLIS],
                       "studio_host": remote.get("studio_host") or ""},
            "local_endpoint": str(saved.get("local_endpoint") or "") if "local_endpoint" in saved else env["local_endpoint"],
            "local_model": str(saved.get("local_model") or "") if "local_model" in saved else env["local_model"]}


def default_brain() -> dict:
    """The brain a new film and a QA look start from: {backend, endpoint, model}."""

    now = saved_settings()
    return {"backend": now["default"], "endpoint": now["local_endpoint"], "model": now["local_model"]}


def valid_host(value, what: str = "host") -> str:
    value = "" if value is None else value
    if not isinstance(value, str) or (value and not HOST.fullmatch(value)):
        raise HTTPException(400, f"{what} is [user@]host[:port], with no spaces or shell characters")
    return value


def save_settings(body: dict) -> dict:
    now = saved_settings()
    if "default" in body:
        if body["default"] not in BACKENDS:
            raise HTTPException(400, "default is one of " + ", ".join(BACKENDS))
        now["default"] = body["default"]
    if "remote" in body:
        remote = body["remote"] or {}
        if not isinstance(remote, dict):
            raise HTTPException(400, "remote is {host, backends, studio_host}")
        backends = remote.get("backends", now["remote"]["backends"])
        if not isinstance(backends, list) or any(b not in CLIS for b in backends):
            raise HTTPException(400, "remote.backends lists CLI brains: " + ", ".join(CLIS))
        now["remote"] = {"host": valid_host(remote.get("host", now["remote"]["host"]), "remote.host"),
                         "backends": list(dict.fromkeys(backends)),
                         "studio_host": valid_host(remote.get("studio_host", now["remote"]["studio_host"]), "remote.studio_host")}
    if "local_endpoint" in body:
        value = str(body["local_endpoint"] or "").strip()
        if value and (len(value) > 300 or not re.fullmatch(r"(?:https?://)?[A-Za-z0-9.\-\[\]:]+(?:/[A-Za-z0-9._~/-]*)?", value)):
            raise HTTPException(400, "local_endpoint is an address like http://127.0.0.1:11434")
        now["local_endpoint"] = value
    if "local_model" in body:
        value = str(body["local_model"] or "").strip()
        if len(value) > 200 or any(c in value for c in "\n\r\0"):
            raise HTTPException(400, "local_model is a model name")
        now["local_model"] = value
    if folder():
        write_json(folder() / "settings.json", now)
    with lock:
        probes.clear()
    return now


def where_of(backend: str, config: dict | None = None) -> str:
    """The ssh host a CLI brain runs on, or "" for this machine."""

    config = config or saved_settings()
    return config["remote"]["host"] if config["remote"]["host"] and backend in config["remote"]["backends"] else ""


# ---------- running a command here or over ssh ----------

def remote_arg(arg: str) -> str:
    # a leading ~/ is left for the remote shell to expand
    return "~/" + shlex.quote(arg[2:]) if arg.startswith("~/") and len(arg) > 2 else shlex.quote(arg)


def remote_line(args: list[str], env: dict | None = None) -> str:
    pairs = [f"{key}={shlex.quote(str(value))}" for key, value in (env or {}).items()]
    return " ".join((["env", *pairs] if pairs else []) + [remote_arg(a) for a in args])


def ssh_of(host: str) -> list[str]:
    name, _, port = host.partition(":")  # [user@]host[:port], checked by HOST
    return SSH + (["-p", port] if port else []) + [name, "--"]


def scp_target(host: str) -> tuple[list[str], str]:
    name, _, port = host.partition(":")
    return ["-P", port] if port else [], name


def search_path() -> str:
    return os.pathsep.join([os.environ.get("PATH", ""), *slate.CLI_DIRS])


def child_env(extra: dict | None = None) -> dict:
    env = {**os.environ, "PATH": search_path(), **(extra or {})}
    env.pop("CLAUDECODE", None)  # a Studio started from inside a Claude Code session must not look like a nested one
    return env


def studio_settings() -> dict:
    """The Studio's own settings.json (Settings): only the Hermes keys are read here, and only when they are well formed."""

    saved = read_json(HOME / "settings.json" if HOME else None, {})
    saved = saved if isinstance(saved, dict) else {}
    profile = str(saved.get("hermes_profile") or "").strip()
    command = str(saved.get("hermes_command") or "").strip()
    return {"hermes_profile": profile if PROFILE.fullmatch(profile) else "",
            "hermes_command": command if command.startswith("/") and os.access(command, os.X_OK) and not os.path.isdir(command) else ""}


def profile_args() -> list[str]:
    profile = studio_settings()["hermes_profile"]
    return ["-p", profile] if profile else []


def cli(name: str) -> str | None:
    if name == "hermes" and studio_settings()["hermes_command"]:
        return studio_settings()["hermes_command"]
    found = shutil.which(CLIS[name][1] if name in CLIS else name, path=search_path())
    if not found and name == "codex" and sys.platform == "darwin":
        found = next(filter(None, (bundled_codex(p) if os.path.isdir(p) else p if os.access(p, os.X_OK) else None for p in MAC_CODEX)), None)
    return found


def bundled_codex(base: str) -> str | None:
    """The binary inside an app's codex-cli folder: codex-package.json's entrypoint, else bin/codex."""

    entry = read_json(Path(base) / "codex-package.json", {})
    entry = str(entry.get("entrypoint") or "") if isinstance(entry, dict) else ""
    for rel in ([entry] if entry and not entry.startswith("/") and ".." not in Path(entry).parts else []) + ["bin/codex"]:
        path = Path(base) / rel
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def from_chatgpt(path: str | None) -> bool:
    return bool(path) and "/ChatGPT.app/" in path


def version_of(path: str) -> str | None:
    try:
        done = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=PROBE_TIMEOUT, env=child_env())
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    return lines[0][:80] if lines else None


def look_here(name: str) -> tuple[str | None, str | None]:
    path = cli(name)
    return path, version_of(path) if path else None


def probe_remote(host: str, names: list[str]) -> dict:
    """{cli: (path, version)} for the CLIs found on another machine, in one ssh call."""

    looks = []
    for name in names:
        extra = " || ls " + " ".join(p + "/bin/codex" if p.endswith("codex-cli") else p for p in MAC_CODEX) + " 2>/dev/null | head -1" if name == "codex" else ""
        looks.append(f'p=$(command -v {shlex.quote(CLIS[name][1])}{extra}) && [ -n "$p" ] && '
                      f'printf "%s\\t%s\\t%s\\n" {name} "$p" "$("$p" --version 2>/dev/null | head -1)"')
    script = REMOTE_PATH + "; ".join(f"( {look} )" for look in looks) + "; true"
    try:
        done = subprocess.run([*ssh_of(host), script], capture_output=True, text=True, timeout=PROBE_TIMEOUT + 2,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return {"__error__": f"{host} did not answer over ssh in {PROBE_TIMEOUT} seconds"}
    if done.returncode == 255:
        return {"__error__": f"ssh to {host} failed: " + (done.stderr.strip()[-200:] or "no answer")}
    found = {}
    for line in done.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0] in names:
            found[parts[0]] = (parts[1], parts[2].strip()[:80] or None)
    return found


def detect(config: dict | None = None, endpoint: str = "") -> list[dict]:
    """Every brain and whether it is there, the slow looks run at once and kept for a minute; never over 10 s."""

    config = config or saved_settings()
    host = config["remote"]["host"]
    remote = config["remote"]["backends"] if host else []
    now = time.monotonic()
    with lock:
        cached = {key: value for key, (at, value) in probes.items() if now - at < PROBE_TTL}
    jobs = {}
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)
    try:
        if remote and (host, "remote") not in cached:
            jobs[(host, "remote")] = pool.submit(probe_remote, host, remote)
        for name in CLIS:
            if name not in remote and ("", name) not in cached:
                jobs[("", name)] = pool.submit(look_here, name)
        jobs[("local", "")] = pool.submit(slate.find_local, endpoint or config["local_endpoint"])
        done, _ = concurrent.futures.wait(jobs.values(), timeout=10)
        fresh = {key: job.result() for key, job in jobs.items() if job in done}
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    with lock:
        for key, value in fresh.items():
            if key[0] != "local":
                probes[key] = (time.monotonic(), value)
    seen = {**cached, **fresh}
    out = []
    for name, (label, _, signin) in CLIS.items():
        item = {"id": name, "label": label, "kind": "cli", "found": False, "where": f"ssh:{host}" if name in remote else "here",
                "path": None, "version": None, "signin": signin, "harness": name in HARNESSES, "session": name in SESSIONS}
        if name == "hermes":
            item.update(profile=studio_settings()["hermes_profile"] or None,
                        note="A conversation here: Hermes reads its MCP servers from its own config, not from the Studio.")
        if name in remote:
            look = seen.get((host, "remote"))
            if look is None:
                item["error"] = f"{host} did not answer in time"
            elif "__error__" in look:
                item["error"] = look["__error__"]
            elif name in look:
                item.update(found=True, path=look[name][0], version=look[name][1])
        else:
            path, version = seen.get(("", name)) or (None, None)
            item.update(found=bool(path), path=path, version=version)
        if name == "codex" and from_chatgpt(item["path"]):
            item["label"] = "Codex (from the ChatGPT app)"
        item.update(signin_flow=recipe_of(name, item["where"] != "here")["flow"], signin_command=terminal_line(name),
                    signin_note=SIGNIN_NOTES.get(name))
        out.append(item)
    base, models = seen.get(("local", "")) or (None, [])
    out.append({"id": "local", "label": "Local model server", "kind": "http", "found": bool(base), "harness": False, "session": False,
                "endpoint": base or slate.endpoint_of(endpoint or config["local_endpoint"]) or None, "models": models or []})
    return out


def found(name: str) -> bool:
    return next((b["found"] for b in detect() if b["id"] == name), False)


def remote_cli(host: str, name: str) -> str:
    """The CLI's path on another machine (asked once a minute), or its bare name for the remote PATH to find."""

    with lock:
        cached = probes.get((host, "remote"))
    if cached and time.monotonic() - cached[0] < PROBE_TTL and name in cached[1]:
        return cached[1][name][0]
    look = probe_remote(host, [name])
    if "__error__" in look:
        raise RuntimeError(look["__error__"])
    if name not in look:
        raise RuntimeError(f"{CLIS[name][0]} is not installed on {host}: install it there and sign in, or run it on this machine")
    return look[name][0]


def run_cli(args: list[str], text: str, host: str, timeout: int) -> subprocess.CompletedProcess:
    """One CLI call in an empty folder of its own, here or on `host`; the prompt goes on stdin."""

    if host:
        line = (REMOTE_PATH + 'd=$(mktemp -d) && cd "$d" && ' + remote_line(args) + '; s=$?; cd / && rm -rf "$d"; exit $s')
        return subprocess.run([*ssh_of(host), line], input=text, capture_output=True, text=True, timeout=timeout)
    with tempfile.TemporaryDirectory() as scratch:  # the brain writes text; it has no files to act on
        return subprocess.run(args, input=text, capture_output=True, text=True, timeout=timeout, cwd=scratch, env=child_env())


def hinted(backend: str, message: str, host: str) -> str:
    where = f"on {host}" if host else "on this machine"
    for pattern, hint in HINTS.get(backend, []):
        if re.search(pattern, message, re.I):
            return hint.format(where=where) + " " + message.strip()[-300:]
    return message.strip()[-400:] or f"{CLIS[backend][0]} gave no answer {where}"


def codex_events(stdout: str) -> tuple[list[dict], str, str]:
    """(events, last agent message, error) from `codex exec --json`."""

    events, reply, error = [], "", ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        events.append(event)
        item = event.get("item") or {}
        if event.get("type") == "item.completed" and item.get("type") == "agent_message" and item.get("text"):
            reply = item["text"]
        elif event.get("type") in ("error", "turn.failed"):
            said = event.get("message") or (event.get("error") or {}).get("message") or ""
            try:  # the server's own error, sent on as JSON text
                said = json.loads(said).get("error", {}).get("message") or said
            except (ValueError, AttributeError):
                pass
            error = said or error
    return events, reply, error


def hermes_events(stdout: str) -> tuple[list[dict], str, str, str]:
    """(events, final text, session id, error) from `hermes chat --format stream-json`; its warnings are plain lines."""

    events, reply, session, error = [], "", "", ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        events.append(event)
        session = str(event.get("session_id") or session)
        if event.get("type") == "result":
            reply = str(event.get("text") or reply)
            if event.get("exit_code") not in (None, 0):
                error = str(event.get("error") or event.get("text") or f"Hermes stopped with code {event.get('exit_code')}")
        elif event.get("type") == "error":
            error = str(event.get("message") or event.get("error") or "Hermes reported an error")
    return events, reply, session, error


def ask(backend: str, system: str, prompt: str, tier: str = "top", endpoint: str = "", model: str = "",
        settings: dict | None = None, timeout: int | None = None) -> str:
    """One answer from a brain: a CLI here or over ssh, or a local OpenAI-compatible server."""

    config, timeout = settings or saved_settings(), timeout or slate.TIMEOUT
    if backend == "local":
        base, models = slate.find_local(endpoint or config["local_endpoint"])
        if not base:
            raise RuntimeError("no local model server answers: start Ollama, LM Studio, vLLM or llama.cpp, or set its address in the Bible")
        body = json.dumps({"model": model or config["local_model"] or (models[0] if models else ""), "temperature": 0.7,
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}).encode()
        request = urllib.request.Request(base + "/chat/completions", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return json.loads(answer.read())["choices"][0]["message"]["content"]
    if backend not in CLIS:
        raise RuntimeError("pick a brain: " + ", ".join(BACKENDS))
    host = where_of(backend, config)
    tool = remote_cli(host, backend) if host else cli(backend)
    if not tool:
        raise RuntimeError(f"{CLIS[backend][0]} is not installed on this machine: install it and sign in, or pick another brain in the Bible")
    joined = f"{system}\n\n---\n\n{prompt}" if system else prompt
    if backend == "codex":
        done = run_cli([tool, "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--json", "-"], joined, host, timeout)
        _, reply, error = codex_events(done.stdout)
        if reply.strip():
            return reply.strip()
        raise RuntimeError(hinted("codex", error or done.stderr or done.stdout or "", host))
    if backend == "gemini":
        done = run_cli([tool, "--output-format", "json"], joined, host, timeout)
        raw = done.stdout.strip()
        try:
            parsed = json.loads(raw[raw.find("{"):]) if "{" in raw else None
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("response"), str) and parsed["response"].strip():
            return parsed["response"].strip()
        if raw and parsed is None and not done.returncode:
            return raw
        error = (parsed or {}).get("error") if isinstance(parsed, dict) else None
        raise RuntimeError(hinted("gemini", (error or {}).get("message", "") if isinstance(error, dict) else str(error or done.stderr), host))
    if backend == "hermes":
        # -z prints only the answer; a prompt too long for one argument goes on stdin to `chat --query-file -` instead
        if len(joined) < 100000:
            done = run_cli([tool, *profile_args(), "-z", joined], "", host, timeout)
            reply = done.stdout.strip()
        else:
            done = run_cli([tool, *profile_args(), "chat", "--query-file", "-", "--format", "stream-json"], joined, host, timeout)
            reply = hermes_events(done.stdout)[1].strip()
        if reply and not done.returncode:
            return reply
        raise RuntimeError(hinted("hermes", done.stderr or done.stdout or f"Hermes stopped with code {done.returncode}", host))
    if backend == "grok":
        done = run_cli([tool, "--prompt-file", "/dev/stdin", "--output-format", "plain"], joined, host, timeout)
        if re.search(r"unexpected argument|unknown (?:option|flag|argument)|unrecognized", done.stderr, re.I):
            done = run_cli([tool, "-p", joined], "", host, timeout)  # an older Grok CLI: the prompt as its argument
        if done.stdout.strip() and not re.search(r"^(?:Internal )?error:", done.stderr, re.I | re.M):
            return done.stdout.strip()
        raise RuntimeError(hinted("grok", done.stderr or done.stdout, host))
    args = [tool, "-p", "--output-format", "json", "--tools", "", "--append-system-prompt", system]
    if slate.TIER_MODEL.get(tier):
        args += ["--model", slate.TIER_MODEL[tier]]
    done = run_cli(args, prompt, host, timeout)
    raw = done.stdout.strip()
    try:
        parsed = json.loads(raw)
    except ValueError:
        if raw:
            return raw
        raise RuntimeError(hinted("claude", done.stderr or "Claude Code gave no answer", host))
    result = parsed.get("result") if isinstance(parsed, dict) else None
    if isinstance(parsed, dict) and parsed.get("is_error"):
        message = result if isinstance(result, str) else "Claude Code returned an error"
        raise RuntimeError(hinted("claude", message, host) if re.search(HINTS["claude"][0][0], message, re.I) else message)
    return result if isinstance(result, str) else raw


# ---------- MCP tools ----------

def default_tools() -> list[dict]:
    starter = Path.home() / "blender_mcp/start_blender_mcp_server.sh"
    blockout = "~/Projects/blockout/mcp/blockout-mcp.mjs"
    return [
        {"id": "blender", "label": "Blender", "command": ["uvx", "blender-mcp"], "env": {"BLENDER_HOST": "127.0.0.1", "BLENDER_PORT": "9876"},
         "via": "here", "host": "", "enabled": True, "start": [str(starter)] if starter.is_file() else []},
        {"id": "blockout", "label": "Blockout", "command": ["node", blockout], "env": {},
         "via": "here" if Path(blockout).expanduser().is_file() else "ssh", "host": "", "enabled": True},
        {"id": "studio", "label": "This Studio", "command": [sys.executable or "python3", str(PACK / "mcp/studio-mcp.py")],
         "env": {"STUDIO_URL": f"http://127.0.0.1:{os.environ.get('PORT', '7870')}"}, "via": "here", "host": "", "enabled": True},
    ]


def tools() -> list[dict]:
    saved = read_json(folder() / "tools.json" if folder() else None, None)
    return saved if isinstance(saved, list) else default_tools()


def tool_of(tool_id: str) -> dict:
    found_ = next((t for t in tools() if t["id"] == tool_id), None)
    if not found_:
        raise HTTPException(404, "no such tool")
    return found_


def valid_command(value, what: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 40 or any(not isinstance(a, str) or not a or len(a) > 1000 or "\0" in a for a in value):
        raise HTTPException(400, f"{what} is a list of words, the program first")
    return value


def valid_tool(tool: dict) -> dict:
    if not isinstance(tool, dict) or not isinstance(tool.get("id"), str) or not TOOL_ID.fullmatch(tool["id"]):
        raise HTTPException(400, "a tool's id is lowercase letters, digits, - and _")
    command = valid_command(tool.get("command"), "command")
    if not command:
        raise HTTPException(400, "command names the program to run")
    env = tool.get("env") or {}
    if not isinstance(env, dict) or any(not ENV_KEY.fullmatch(str(k)) or not isinstance(v, str) or "\0" in v for k, v in env.items()):
        raise HTTPException(400, "env is {NAME: value}")
    if tool.get("via", "here") not in ("here", "ssh"):
        raise HTTPException(400, 'via is "here" or "ssh"')
    out = {"id": tool["id"], "label": str(tool.get("label") or tool["id"])[:80], "command": command, "env": env,
           "via": tool.get("via", "here"), "host": valid_host(tool.get("host") or "", "host"), "enabled": tool.get("enabled", True) is not False}
    for key in ("start", "remote_command"):
        if tool.get(key):
            out[key] = valid_command(tool[key], key)
    return out


def save_tools(body: dict) -> list[dict]:
    if isinstance(body.get("tools"), list):
        fresh = [valid_tool(t) for t in body["tools"]]
        if len({t["id"] for t in fresh}) != len(fresh):
            raise HTTPException(400, "two tools have the same id")
    else:
        one, fresh = valid_tool(body), tools()
        at = next((i for i, t in enumerate(fresh) if t["id"] == one["id"]), len(fresh))
        fresh[at:at + 1] = [one]
    if folder():
        write_json(folder() / "tools.json", fresh)
    return fresh


def local_command(tool: dict) -> list[str]:
    command = [os.path.expanduser(a) if a.startswith("~/") else a for a in tool["command"]]
    if "/" not in command[0]:
        command[0] = shutil.which(command[0], path=search_path()) or command[0]
    return command


def launch_of(tool: dict, config: dict | None = None) -> tuple[list[str], dict]:
    """(argv, env) that starts the tool from this machine."""

    if tool["via"] == "ssh":
        host = tool["host"] or (config or saved_settings())["remote"]["host"]
        if not host:
            raise RuntimeError(f"{tool['label']} runs over ssh: set its host, or the remote host in the brain settings")
        return [*ssh_of(host), REMOTE_PATH + "exec " + remote_line(tool["command"], tool["env"])], {}
    return local_command(tool), dict(tool["env"])


def remote_launch(tool: dict, harness_host: str, config: dict) -> tuple[list[str], dict]:
    """(argv, env) that starts the tool from the harness's host (see the module's notes)."""

    if tool.get("remote_command"):
        return list(tool["remote_command"]), dict(tool["env"])
    if tool["via"] == "ssh":
        host = tool["host"] or config["remote"]["host"]
        if host == harness_host:
            return list(tool["command"]), dict(tool["env"])
        return [*ssh_of(host), REMOTE_PATH + "exec " + remote_line(tool["command"], tool["env"])], {}
    studio = config["remote"]["studio_host"]
    if not studio:
        raise RuntimeError(f"{tool['label']} runs on this machine and the agent runs on {harness_host}: set remote.studio_host "
                           f"(how {harness_host} reaches this machine over ssh) or give the tool a remote_command")
    return [*ssh_of(studio), REMOTE_PATH + "exec " + remote_line(tool["command"], tool["env"])], {}


def rpc_send(process, message: dict) -> None:
    process.stdin.write((json.dumps(message) + "\n").encode())
    process.stdin.flush()


def check_tool(tool: dict) -> dict:
    """Start the stdio server, shake hands, list its tools, stop it."""

    try:
        argv, env = launch_of(tool)
    except RuntimeError as error:
        return {"ok": False, "tools": [], "error": str(error)}
    started = time.time()
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=child_env(env), start_new_session=True)
    except OSError as error:
        return {"ok": False, "tools": [], "error": f"{argv[0]} could not start: {error}"}
    lines: "queue.Queue[bytes | None]" = queue.Queue()
    errors: list[bytes] = []
    def read():
        for line in process.stdout:
            lines.put(line)
        lines.put(None)
    threading.Thread(target=read, daemon=True).start()
    threading.Thread(target=lambda: errors.extend(process.stderr), daemon=True).start()

    def answer(wanted: int):
        while True:
            left = CHECK_TIMEOUT - (time.time() - started)
            if left <= 0:
                raise TimeoutError
            line = lines.get(timeout=left)
            if line is None:
                raise EOFError
            try:
                message = json.loads(line)
            except ValueError:
                continue  # a server's stray print
            if isinstance(message, dict) and message.get("id") == wanted:
                return message

    out = {"ok": False, "tools": [], "error": None}
    try:
        rpc_send(process, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "slate-studio", "version": "1"}}})
        hello = answer(1)
        if hello.get("error"):
            raise RuntimeError(hello["error"].get("message") or "initialize failed")
        out["server"] = (hello.get("result") or {}).get("serverInfo")
        rpc_send(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        rpc_send(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listed = answer(2)
        if listed.get("error"):
            raise RuntimeError(listed["error"].get("message") or "tools/list failed")
        out.update(ok=True, tools=[t.get("name") for t in (listed.get("result") or {}).get("tools", []) if t.get("name")])
    except (TimeoutError, queue.Empty):
        out["error"] = f"no answer in {CHECK_TIMEOUT} seconds"
    except (EOFError, BrokenPipeError, OSError):
        out["error"] = "the server stopped before it answered"
    except RuntimeError as error:
        out["error"] = str(error)
    finally:
        stop(process)
    if not out["ok"]:
        said = b"".join(errors).decode(errors="replace").strip()
        if said:
            out["error"] += ": " + said[-400:]
    out["elapsedMs"] = int(1000 * (time.time() - started))
    return out


def stop(process) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def listening(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def start_blender() -> dict:
    tool = tool_of("blender")
    port = int(tool["env"].get("BLENDER_PORT") or 9876)
    if tool["via"] != "here":
        raise HTTPException(400, "Blender runs on another machine for this tool: start it there")
    log = folder() / "blender.log"
    if listening(port):
        return {"listening": True, "started": False, "port": port, "log": str(log.relative_to(HOME)) if log.exists() else None}
    if not tool.get("start"):
        raise HTTPException(400, "no start command is set for Blender: open Blender with the Blender MCP add-on and press Connect")
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as handle:
        process = subprocess.Popen(local_command({**tool, "command": tool["start"]}), stdin=subprocess.DEVNULL, stdout=handle,
                                   stderr=subprocess.STDOUT, env=child_env(), start_new_session=True, cwd=str(HOME))
    for _ in range(10):  # the add-on opens its socket a few seconds after Blender starts
        if listening(port) or process.poll() is not None:
            break
        time.sleep(1)
    return {"listening": listening(port), "started": process.poll() is None, "pid": process.pid, "port": port,
            "log": str(log.relative_to(HOME)), "exit": process.poll()}


# ---------- agent tasks ----------

def task_folder(task_id: str) -> Path:
    return HOME / "agent" / task_id


def work_folder(task: dict) -> Path:
    """Where the harness runs: the thread's own folder (the same one each turn, so its session is found again), or for a
    task from before threads, the task's folder."""

    return HOME / "agent" / task["thread"] if task.get("thread") else task_folder(task["id"])


def save_task(task: dict) -> None:
    write_json(task_folder(task["id"]) / "task.json", {k: v for k, v in task.items() if not k.startswith("_")})


def log_tail(task: dict) -> str:
    try:
        text = (task_folder(task["id"]) / "log.txt").read_text(errors="replace")
    except OSError:
        return ""
    return text[-LOG_LIMIT:]


def public(task: dict, log: bool = True) -> dict:
    out = {k: task.get(k) for k in ("id", "status", "prompt", "backend", "tools", "attach", "where", "created", "started",
                                    "finished", "reply", "outputs", "error", "thread")}
    out["steps"], out["jobs"] = task.get("steps") or [], task.get("jobs") or []
    if log:
        out["log"] = log_tail(task)
    return out


def load_tasks() -> None:
    root = HOME / "agent"
    for file in sorted(root.glob("*/task.json")) if root.is_dir() else []:
        task = read_json(file, None)
        if not isinstance(task, dict) or task.get("id") != file.parent.name:
            continue
        if task.get("status") == "running":
            task.update(status="failed", error="the Studio stopped while this task ran", finished=task.get("finished") or time.time())
            save_task(task)
        tasks[task["id"]] = task
        if task.get("status") == "queued":
            pending.put(task["id"])


def ensure_worker() -> None:
    with lock:
        if worker["thread"] is None or not worker["thread"].is_alive():
            worker["thread"] = threading.Thread(target=work, daemon=True)
            worker["thread"].start()


def work() -> None:
    while True:
        task_id = pending.get()
        task = tasks.get(task_id)
        if not task or task["status"] != "queued":
            continue
        try:
            perform(task)
        except Exception as error:  # the reason goes to the page as it is
            if task["status"] != "cancelled":
                task.update(status="failed", error=str(error)[:800])
        finally:
            with lock:
                running.update(id=None, process=None, remote=None)
            task["finished"] = time.time()
            task["outputs"] = outputs_of(task)
            save_task(task)
            finish_turn(task)


# ---------- threads: the Agent room's conversations ----------

def threads_folder() -> Path:
    return folder() / "threads"


def save_thread(thread: dict) -> None:
    write_json(threads_folder() / f"{thread['id']}.json", thread)


def turn(role: str, text: str, **more) -> dict:
    return {"id": uuid.uuid4().hex[:8], "role": role, "text": text, "attachments": [], "at": time.time(), **more}


def question_of(text: str) -> tuple[bool, str, list[str]]:
    """(is it a question, the text to show, the choices it offers): the `[question]` marker the room's instructions ask
    for, or an answer whose last line ends with a question mark."""

    text = (text or "").strip()
    marked = QUESTION.search(text)
    if marked:
        before, after = text[:marked.start()].rstrip(), text[marked.end():].strip()
        lines = after.splitlines()
        choices, kept = [], []
        for line in lines:
            item = re.match(r"\s*(?:[-*•]|\d{1,2}[.)])\s+(.+)", line)
            if item and len(choices) < 6 and len(item.group(1)) <= 120:
                choices.append(item.group(1).strip().strip("*").strip())
            elif line.strip():
                kept.append(line.rstrip())
        shown = "\n\n".join(part for part in (before, "\n".join(kept).strip()) if part)
        return True, shown or after, choices
    last = next((line for line in reversed(text.splitlines()) if line.strip()), "")
    last = re.sub(r"[*_`\"'”’)\]\s]+$", "", last)
    return bool(last) and last.endswith("?"), text, []


def recent(thread: dict, limit: int = HISTORY_CHARS) -> str:
    """The thread's turns so far, newest last, as a brain without a session reads them ahead of the message."""

    lines, size = [], 0
    for item in reversed(thread.get("turns") or []):
        if item["role"] not in ("user", "agent") or not (item.get("text") or "").strip():
            continue
        line = ("Filmmaker: " if item["role"] == "user" else "You: ") + item["text"].strip()
        if size + len(line) > limit:
            break
        lines.append(line)
        size += len(line)
    return "\n\n".join(reversed(lines))


def with_history(thread: dict | None, message: str, skip_last: bool = True) -> str:
    """The message with the conversation so far ahead of it (the newest user turn is the message itself)."""

    if not thread:
        return message
    earlier = dict(thread, turns=(thread.get("turns") or [])[:-1] if skip_last else thread.get("turns") or [])
    said = recent(earlier)
    return f"The conversation so far:\n\n{said}\n\n---\n\nThe filmmaker now says:\n\n{message}" if said else message


def summary(thread: dict) -> dict:
    """A thread as the list shows it."""

    task = tasks.get(thread.get("pending") or "")
    said = [t for t in thread.get("turns") or [] if t["role"] in ("user", "agent")]
    last = said[-1] if said else None
    return {k: thread.get(k) for k in ("id", "title", "backend", "tools", "created", "updated")} | {
        "turns": len(said), "status": task["status"] if task and task["status"] in ("queued", "running") else "idle",
        "waiting": bool(last and last["role"] == "agent" and last.get("question") and not thread.get("pending")),
        "last": (last or {}).get("text", "")[:160], "delivered": sum(len(t.get("attachments") or []) for t in said if t["role"] == "agent")}


def changed_files(task: dict) -> list[str]:
    """The files of the work folder that this turn made or changed, as Library paths."""

    here, before = work_folder(task), task.get("_before") or {}
    skip = {"task.json", "mcp.json", "log.txt", "stream.jsonl", "stderr.txt", ".pid", *task.get("inputs", [])}
    out = []
    for name, seen in snapshot(here).items():
        if name not in skip and before.get(name) != seen:
            out.append(str((here / name).relative_to(HOME)))
    return out[:500]


def snapshot(here: Path) -> dict:
    out = {}
    for path in sorted(here.rglob("*")) if here.is_dir() else []:
        name = str(path.relative_to(here))
        if path.is_file() and not any(part.startswith(".") for part in Path(name).parts):
            stat = path.stat()
            out[name] = [stat.st_size, stat.st_mtime_ns]
    return out


def live_of(thread: dict) -> dict | None:
    """The turn being worked on: its task's status, its working lines and what it has made so far."""

    task = tasks.get(thread.get("pending") or "")
    if not task:
        return None
    made = changed_files(task) if task["status"] == "running" and not task.get("_host") and task.get("_before") is not None else task.get("outputs") or []
    return {"task": task["id"], "status": task["status"], "backend": task["backend"], "where": task.get("where"),
            "started": task.get("started"), "steps": (task.get("steps") or [])[-60:], "jobs": task.get("jobs") or [],
            "outputs": made[:60], "error": task.get("error")}


def thread_public(thread: dict) -> dict:
    return {**{k: v for k, v in thread.items() if k != "session"}, "session": bool(thread.get("session")),
            "live": live_of(thread), **{k: v for k, v in summary(thread).items() if k in ("status", "waiting")}}


def thread_of(thread_id: str) -> dict:
    thread = threads.get(thread_id) if isinstance(thread_id, str) else None
    if not thread:
        raise HTTPException(404, "no such conversation")
    return thread


def finish_turn(task: dict) -> None:
    """The agent's answer, its working lines and what it made, into its thread."""

    with lock:
        thread = threads.get(task.get("thread") or "")
        if not thread or thread.get("pending") != task["id"] or task["status"] in ("queued", "running"):
            return
        stamp = task.get("finished") or time.time()
        for step in [s for s in task.get("steps") or [] if not s.get("live")][-60:]:
            thread["turns"].append(turn("tool", step["text"], at=step.get("at") or stamp, task=task["id"]))
        asked, shown, choices = question_of(task.get("reply") or "") if task["status"] == "done" else (False, task.get("reply") or "", [])
        thread["turns"].append(turn("agent", shown[:TEXT_LIMIT], at=stamp, task=task["id"], backend=task["backend"], status=task["status"],
                                    question=asked, options=choices, jobs=task.get("jobs") or [], error=task.get("error"),
                                    attachments=list(task.get("outputs") or [])))
        if task.get("_session"):
            thread["session"] = task["_session"]
        thread.update(pending=None, updated=stamp)
        save_thread(thread)


def load_threads() -> None:
    for file in sorted(threads_folder().glob("th-*.json")) if folder() and threads_folder().is_dir() else []:
        thread = read_json(file, None)
        if not isinstance(thread, dict) or thread.get("id") != file.stem or not THREAD_ID.fullmatch(file.stem):
            continue
        thread.setdefault("turns", [])
        threads[thread["id"]] = thread
        task = tasks.get(thread.get("pending") or "")
        if thread.get("pending") and not task:
            thread["pending"] = None
            save_thread(thread)
        elif task and task["status"] not in ("queued", "running"):
            finish_turn(task)  # the Studio stopped while it ran: its answer (or why it failed) goes into the thread


def new_thread(backend: str, tools_: list[str], title: str) -> dict:
    now = time.time()
    thread = {"id": time.strftime("th-%m%d-%H%M%S-") + uuid.uuid4().hex[:6], "title": " ".join(title.split())[:80] or "A conversation",
              "backend": backend, "tools": tools_, "created": now, "updated": now, "session": None, "pending": None, "turns": []}
    threads[thread["id"]] = thread
    return thread


def rename_thread(thread_id: str, body: dict) -> dict:
    thread = thread_of(thread_id)
    title = " ".join(str(body.get("title") or "").split())[:80]
    if not title:
        raise HTTPException(400, "a conversation's title is some words")
    with lock:
        thread.update(title=title)
        save_thread(thread)
    return thread_public(thread)


def forget_thread(thread_id: str) -> dict:
    """The conversation's record goes; what it made stays in the Library."""

    thread = thread_of(thread_id)
    if thread.get("pending"):
        raise HTTPException(409, "the agent is still working on this conversation: stop it first")
    with lock:
        threads.pop(thread_id, None)
        try:
            (threads_folder() / f"{thread_id}.json").unlink()
        except OSError:
            pass
    return {"id": thread_id, "removed": True}


def submit(body: dict) -> dict:
    prompt = str(body.get("prompt") or body.get("text") or "").strip()
    if not prompt or len(prompt) > TEXT_LIMIT:
        raise HTTPException(400, "a prompt is needed (up to 20,000 characters)")
    thread = thread_of(body["thread"]) if body.get("thread") else None
    if thread and thread.get("pending"):
        raise HTTPException(409, "the agent is still working on this conversation: wait for it, or stop it")
    backend = body.get("backend") or (thread or {}).get("backend") or (
        saved_settings()["default"] if saved_settings()["default"] in HARNESSES else "claude")
    if backend not in BACKENDS:
        raise HTTPException(400, "an agent is one of " + ", ".join(BACKENDS))
    known = {t["id"]: t for t in tools()}
    chosen = body.get("tools") if body.get("tools") is not None else [t for t, v in known.items() if v["enabled"]]
    if not isinstance(chosen, list) or any(t not in known for t in chosen):
        raise HTTPException(400, "tools are ids from /api/agent/tools")
    if backend in HARNESSES and any(not known[t]["enabled"] for t in chosen):
        raise HTTPException(400, "a chosen tool is turned off")
    chosen = list(dict.fromkeys(chosen)) if backend in HARNESSES else []  # a conversation has no tools to run
    attach_ = body.get("attach") or []
    if not isinstance(attach_, list) or len(attach_) > 20:
        raise HTTPException(400, "attach is a list of up to 20 Library paths")
    for path in attach_:
        resolved = (HOME / str(path)).resolve()
        if HOME.resolve() not in resolved.parents or not resolved.is_file():
            raise HTTPException(400, f"{path} is not a file in the studio folder")
    task_id = time.strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    host = where_of(backend) if backend in CLIS else ""
    with lock:
        thread = thread or new_thread(backend, chosen, prompt)
        thread["turns"].append(turn("user", prompt, attachments=[str(p) for p in attach_], task=task_id))
        thread.update(backend=backend, tools=chosen, pending=task_id, updated=time.time())
        task = {"id": task_id, "status": "queued", "prompt": prompt, "backend": backend, "tools": chosen,
                "attach": [str(p) for p in attach_], "where": f"ssh:{host}" if host else "here", "created": time.time(),
                "started": None, "finished": None, "reply": "", "outputs": [], "error": None, "inputs": [], "thread": thread["id"],
                "steps": [], "jobs": []}
        task_folder(task_id).mkdir(parents=True, exist_ok=True)
        (task_folder(task_id) / "log.txt").write_text("")
        tasks[task_id] = task
        save_task(task)
        save_thread(thread)
    pending.put(task_id)
    ensure_worker()
    return public(task)


def cancel(task_id: str) -> dict:
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(404, "no such task")
    with lock:
        if task["status"] == "queued":
            task.update(status="cancelled", finished=time.time())
            save_task(task)
            finish_turn(task)
        elif task["status"] == "running":
            task["status"] = "cancelled"
            process = running["process"] if running["id"] == task_id else None
            if process is not None:
                stop(process)
            if running.get("remote") and running["id"] == task_id:
                host, where = running["remote"]
                try:  # the harness on the other machine, by the pid it wrote
                    subprocess.run([*ssh_of(host), f"kill -TERM $(cat {shlex.quote(where)}/.pid) 2>/dev/null; true"],
                                   capture_output=True, timeout=PROBE_TIMEOUT + 2, stdin=subprocess.DEVNULL)
                except (OSError, subprocess.TimeoutExpired):
                    pass
    return public(task)


def outputs_of(task: dict) -> list[str]:
    if task.get("thread"):
        return changed_files(task) if task.get("_before") is not None else list(task.get("outputs") or [])
    here = task_folder(task["id"])
    skip = {"task.json", "mcp.json", "log.txt", "stream.jsonl", "stderr.txt", ".pid", *task.get("inputs", [])}
    out = []
    for path in sorted(here.rglob("*")) if here.is_dir() else []:
        name = str(path.relative_to(here))
        if path.is_file() and name not in skip and not any(part.startswith(".") for part in Path(name).parts):
            out.append(str(path.relative_to(HOME)))
    return out[:500]


def mcp_servers(task: dict, harness_host: str, config: dict) -> dict:
    servers = {}
    for tool_id in task["tools"]:
        tool = tool_of(tool_id)
        argv, env = remote_launch(tool, harness_host, config) if harness_host else launch_of(tool, config)
        servers[tool_id] = {"command": argv[0], "args": argv[1:], **({"env": env} if env else {})}
    return servers


def toml_value(value) -> str:
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k if ENV_KEY.fullmatch(k) else json.dumps(k)} = {json.dumps(v)}" for k, v in value.items()) + "}"
    return json.dumps(value)  # a JSON string or list of strings is TOML too


def harness_args(task: dict, tool: str, config_path: str, servers: dict, system: str, session: str | None = None) -> list[str]:
    """The harness's command line; `session` is the id of the thread's session to continue, None for a first turn."""

    if task["backend"] == "claude":
        allowed = [f"mcp__{name}" for name in servers] + ["Read", "Write", "Glob"]
        args = [tool, "-p", "--output-format", "stream-json", "--verbose", "--mcp-config", config_path, "--strict-mcp-config",
                "--tools", "Read,Write,Glob", "--allowedTools", ",".join(allowed), "--permission-mode", "acceptEdits",
                "--append-system-prompt", system]
        if task.get("thread"):  # a session of its own, named by the Studio on the first turn and resumed after
            args += ["--resume", session] if session else ["--session-id", task["_session"]["id"]]
        return args
    if task["backend"] == "hermes":
        args = [tool, *profile_args(), "chat", "--query-file", "-", "--format", "stream-json"]
        return args + (["--resume", session] if session else ["--continue", "imagination-" + task["thread"], "--create-if-missing"])
    # `codex exec resume` takes no --sandbox: the same sandbox goes as its config value
    args = ([tool, "exec", "resume", "--skip-git-repo-check", "--json", "-c", 'sandbox_mode="workspace-write"'] if session
            else [tool, "exec", "--skip-git-repo-check", "--sandbox", "workspace-write", "--json"])
    for name, server in servers.items():
        for key in ("command", "args", "env"):
            if key in server:
                args += ["-c", f"mcp_servers.{name}.{key}={toml_value(server[key])}"]
    return args + ([session] if session else []) + ["-"]


def tool_name(name: str) -> str:
    # mcp__studio__queue_clip -> studio · queue_clip
    return " · ".join(part for part in str(name).removeprefix("mcp__").split("__") if part) or str(name)


def results_in(value) -> list:
    """The JSON values a tool answered with, wherever the harness put its text."""

    found = []
    if isinstance(value, str):
        try:
            found.append(json.loads(value))
        except ValueError:
            pass
    elif isinstance(value, list):
        for item in value:
            found += results_in(item)
    elif isinstance(value, dict):
        for key in ("text", "content", "result", "structuredContent", "output"):
            if key in value:
                found += results_in(value[key])
    return found


def note_results(task: dict, value) -> list[str]:
    """Jobs the agent queued in the Studio, from its tools' answers, and a working line for each change of state."""

    out = []
    for result in results_in(value):
        if not (isinstance(result, dict) and isinstance(result.get("id"), str) and result.get("kind") and result.get("status")):
            continue
        jobs_ = task.setdefault("jobs", [])
        if result["id"] not in jobs_:
            jobs_.append(result["id"])
        said = f"{result['kind']} {result['status']}" + (f": {result['title']}" if result.get("title") else "")
        if result.get("outputs"):
            said += " → " + ", ".join(str(p).split("/")[-1] for p in result["outputs"][:3])
        if said not in [s["text"] for s in task.get("steps") or []][-8:]:
            out.append(said)
    return out


def render(backend: str, line: str, task: dict) -> str:
    """One line of the harness's stream as a person reads it; working lines also go to the task's steps."""

    try:
        event = json.loads(line)
    except ValueError:
        return line.rstrip("\n") + "\n" if line.strip() else ""
    if not isinstance(event, dict):
        return ""
    out, steps = [], []
    if backend == "claude":
        if event.get("session_id") and task.get("_session"):
            task["_session"]["id"] = str(event["session_id"])
        if event.get("type") == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and block.get("text", "").strip():
                    out.append(block["text"].strip())
                elif block.get("type") == "tool_use":
                    out.append("→ " + str(block.get("name")))
                    steps.append("→ " + tool_name(block.get("name")))
        elif event.get("type") == "user":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    steps += note_results(task, block.get("content"))
        elif event.get("type") == "result":
            if isinstance(event.get("result"), str):
                task["reply"] = event["result"]
            if event.get("is_error"):
                task["_error"] = hinted("claude", str(event.get("result") or "Claude Code returned an error"), task["_host"])
        elif event.get("type") == "system" and event.get("subtype") == "init":
            ready = [s.get("name") + (" (" + s.get("status") + ")" if s.get("status") != "connected" else "") for s in event.get("mcp_servers") or []]
            out.append("tools: " + (", ".join(ready) or "none"))
            steps.append("tools: " + (", ".join(ready) or "none"))
    elif backend == "hermes":
        _, reply, session, error = hermes_events(line)
        if session and task.get("_session") is not None:
            task["_session"]["id"] = session
        kind = event.get("type")
        if kind == "text" and str(event.get("text") or "").strip():
            out.append(str(event["text"]).strip())
        elif kind in ("tool", "tool_call", "tool_use", "tool_start") and (event.get("name") or event.get("tool")):
            out.append("→ " + str(event.get("name") or event.get("tool")))
            steps.append(out[-1])
        elif kind == "result":
            task["reply"] = reply or task.get("reply") or ""
        if error:
            task["_error"] = hinted("hermes", error, task["_host"])
            out.append("error: " + error)
    else:
        _, reply, error = codex_events(line)
        item = event.get("item") or {}
        if event.get("type") == "thread.started" and event.get("thread_id") and task.get("_session") is not None:
            task["_session"]["id"] = str(event["thread_id"])
        if reply:
            task["reply"] = reply
            out.append(reply.strip())
        elif error:
            task["_error"] = hinted("codex", error, task["_host"])
            out.append("error: " + error)
        elif event.get("type") == "item.started" and item.get("type") == "mcp_tool_call":
            out.append(f"→ {item.get('server')}.{item.get('tool')}")
            steps.append(f"→ {item.get('server')} · {item.get('tool')}")
        elif event.get("type") == "item.completed" and item.get("type") == "mcp_tool_call":
            steps += note_results(task, item.get("result"))
        elif event.get("type") == "item.started" and item.get("type") == "command_execution":
            out.append("$ " + str(item.get("command"))[:300])
            steps.append("$ " + str(item.get("command"))[:120])
    if steps:
        task.setdefault("steps", []).extend({"text": s[:300], "at": time.time()} for s in steps)
        del task["steps"][:-STEP_LIMIT]
    return "".join(text + "\n" for text in out)


def converse(task: dict, thread: dict | None, log) -> None:
    """A brain with no session the Studio can name (Gemini, Grok, a local model): one answer, the thread's recent turns
    read ahead of the message. Its call is not a process the Studio holds, so Stop leaves it to finish and drops it."""

    log.write(f"[{task['backend']} {task['where']}] {task['prompt'][:200]}\n")
    log.flush()
    system = CHAT + ROOM.format(tools="")
    answer: dict = {}

    def run():
        try:
            answer["text"] = ask(task["backend"], system, with_history(thread, task["prompt"]))
        except Exception as error:  # the reason goes to the page as it is
            answer["error"] = str(error)[:800]
    caller = threading.Thread(target=run, daemon=True)
    caller.start()
    task.setdefault("steps", []).append({"text": "thinking…", "at": time.time(), "live": True})  # shown while it runs, not kept
    ends = time.time() + TASK_TIMEOUT
    while caller.is_alive() and task["status"] != "cancelled" and time.time() < ends:
        caller.join(0.25)
    if task["status"] == "cancelled":
        return
    if caller.is_alive():
        raise RuntimeError(f"stopped after {TASK_TIMEOUT // 3600} hours")
    if "error" in answer:
        raise RuntimeError(answer["error"])
    task["reply"] = answer.get("text") or ""
    log.write(task["reply"] + "\n")
    task["status"] = "done"


def stream(task: dict, argv: list[str], cwd: str | None, text: str, log) -> int:
    """Run the harness, its stream to the log and the task's steps; the return code (None when cancelled first)."""

    here = task_folder(task["id"])
    with lock:
        if task["status"] == "cancelled":
            return None
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(here / "stderr.txt", "w"),
                                   text=True, cwd=cwd, env=child_env(), start_new_session=True)
        running.update(id=task["id"], process=process)
    process.stdin.write(text)
    process.stdin.close()
    timer = threading.Timer(TASK_TIMEOUT, lambda: (task.update(_error=f"stopped after {TASK_TIMEOUT // 3600} hours"), stop(process)))
    timer.daemon = True
    timer.start()
    with open(here / "stream.jsonl", "a") as raw:
        for line_ in process.stdout:
            raw.write(line_)
            shown = render(task["backend"], line_, task)
            if shown:
                log.write(shown)
                log.flush()
    process.wait()
    timer.cancel()
    return process.returncode


def perform(task: dict) -> None:
    config = saved_settings()
    thread = threads.get(task.get("thread") or "")
    host = where_of(task["backend"], config) if task["backend"] in CLIS else ""
    task.update(status="running", started=time.time(), _host=host)
    save_task(task)
    here, work_ = task_folder(task["id"]), work_folder(task)
    work_.mkdir(parents=True, exist_ok=True)
    log = open(here / "log.txt", "a")
    try:
        if task["backend"] not in SESSIONS:
            return converse(task, thread, log)
        names = []
        for path in task["attach"]:  # the attachments, beside what the agent makes
            source = (HOME / path).resolve()
            name = source.name if source.name not in names else f"{len(names)}_{source.name}"
            if (work_ / name).exists() and not os.path.samefile(work_ / name, source):
                name = f"{task['id']}_{name}"  # a different file of the same name from an earlier turn
            if not (work_ / name).exists():
                try:
                    os.link(source, work_ / name)
                except OSError:
                    shutil.copy2(source, work_ / name)
            names.append(name)
        task["inputs"] = names
        task["_before"] = snapshot(work_)
        servers = mcp_servers(task, host, config) if task["backend"] in HARNESSES else {}
        if task["backend"] in HARNESSES:
            write_json(work_ / "mcp.json", {"mcpServers": servers})
        where_s = f"ssh:{host}" if host else "here"
        session = (thread or {}).get("session") or {}
        continuing = session.get("id") if thread and session.get("backend") == task["backend"] and session.get("where") == where_s else None
        if host:
            tool = remote_cli(host, task["backend"])
            # a thread keeps one folder on the other machine, so its session is found there again; a task of its own gets a fresh one
            line = ("mkdir -p ~/.slate-studio-agent/" + task["thread"] + " && cd ~/.slate-studio-agent/" + task["thread"] + " && pwd"
                    if task.get("thread") else "mkdir -p ~/.slate-studio-agent && mktemp -d ~/.slate-studio-agent/" + task["id"] + ".XXXXXX")
            made = subprocess.run([*ssh_of(host), line], capture_output=True, text=True, timeout=PROBE_TIMEOUT + 4, stdin=subprocess.DEVNULL)
            where = made.stdout.strip()
            if made.returncode or not where.startswith("/"):
                raise RuntimeError(f"could not make a working folder on {host}: " + (made.stderr.strip()[-300:] or "no answer"))
            port, name = scp_target(host)
            sending = [str(work_ / n) for n in (["mcp.json"] if task["backend"] in HARNESSES else []) + names]
            if sending:
                sent = subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", *port, *sending, f"{name}:{where}/"],
                                      capture_output=True, text=True, timeout=600)
                if sent.returncode:
                    raise RuntimeError(f"could not copy the task's files to {host}: " + sent.stderr.strip()[-300:])
            studio = f" Tools that run on the Studio's own machine (Blender, the Studio) save into {work_} there."
            folder_ = f" ({where} on {host})"
            config_path = f"{where}/mcp.json"
            with lock:
                running["remote"] = (host, where)
        else:
            tool = cli(task["backend"])
            if not tool:
                raise RuntimeError(f"{CLIS[task['backend']][0]} is not installed on this machine: install it and sign in, or run it over ssh")
            studio, folder_, config_path, where = "", f" ({work_})", str(work_ / "mcp.json"), None
        system = SYSTEM.format(folder=folder_, studio=studio)
        if task.get("thread"):
            system += ROOM.format(tools=ROOM_TOOLS.format(names=STUDIO_TOOLS) if "studio" in task["tools"] else "") \
                if task["backend"] in HARNESSES else NO_STUDIO + ROOM.format(tools="")
        log.write(f"[{task['backend']} {'on ' + host if host else 'here'}{' · continues its session' if continuing else ''}] {task['prompt'][:200]}\n")
        log.flush()
        for attempt in (0, 1):
            task.update(reply="", _session={"backend": task["backend"], "id": continuing or (str(uuid.uuid4()) if task["backend"] == "claude" else ""),
                                            "where": where_s} if task.get("thread") else None)
            task.pop("_error", None)
            message = task["prompt"] if continuing else with_history(thread, task["prompt"])
            # Claude Code takes the instruction as its system prompt; Codex and Hermes read it ahead of a first turn
            text = message if task["backend"] == "claude" or continuing else f"{system}\n\n---\n\n{message}"
            args = harness_args(task, tool, config_path, servers, system, continuing)
            argv, cwd = ([*ssh_of(host), REMOTE_PATH + f"cd {shlex.quote(where)} && echo $$ > .pid && exec " + remote_line(args)], None) if host \
                else (args, str(work_))
            code = stream(task, argv, cwd, text, log)
            if code is None or task["status"] == "cancelled":
                break
            said = (here / "stderr.txt").read_text(errors="replace")
            if continuing and (code or task.get("_error")) and LOST_SESSION.search(said + " " + str(task.get("_error") or "")):
                log.write("[the agent's session was not found: starting it again with the conversation so far]\n")
                continuing = None
                continue
            break
        if host:  # what the agent made, back into the thread's folder; nothing here is removed
            shell = " ".join(ssh_of(host)[:-2])  # ssh with its options and port, as rsync runs it
            back = subprocess.run(["rsync", "-a", "--exclude", ".pid", "--exclude", "mcp.json", "-e", shell, f"{name}:{where}/", f"{work_}/"]
                                  if shutil.which("rsync") else ["scp", "-q", "-r", "-o", "BatchMode=yes", *port, f"{name}:{where}/.", str(work_)],
                                  capture_output=True, text=True, timeout=1800)
            if back.returncode:
                log.write(f"could not copy the outputs back from {host}: {back.stderr.strip()[-300:]}\n")
        if task["status"] == "cancelled" or code is None:
            return
        error = task.pop("_error", None)
        if code or error:
            said = (here / "stderr.txt").read_text(errors="replace").strip()
            task.update(status="failed", error=error or hinted(task["backend"], said or f"{task['backend']} stopped with code {code}", host))
        else:
            task["status"] = "done"
        if task["status"] != "done" or not (task.get("_session") or {}).get("id"):
            task["_session"] = None  # the thread keeps the session it had: only a finished turn names a new one
    finally:
        log.close()
        task.pop("_host", None)
        task.pop("_error", None)


# ---------- signing a brain in: the CLI's own login, in a pseudo-terminal ----------
#
# The Studio never sees a password or keeps a token: it starts the CLI's own login command (here, or on the brains'
# SSH host with `ssh -tt`) in a pty, reads what it prints and shows the page the parts a person acts on, the sign-in
# address, a device code to type there, or a field for the code the sign-in page hands back, which goes to the CLI's
# terminal and nowhere else. The CLI stores its own credentials where it always does. One sign-in at a time, stopped
# after SIGNIN_TIMEOUT; nothing of it is written to disk. States: starting, needs_browser {url}, needs_code {url,
# prompt}, device_code {url, code}, verifying (a code was sent), done, failed {reason}.
#
# The recipes (from each CLI's --help on a Mac, October 2026):
#   claude  `claude auth login --claudeai`: prints a claude.ai address and asks for the code that page shows.
#   codex   `codex login`: a browser sign-in whose callback lands on localhost of the CLI's machine, so only when the
#           CLI runs here; `codex login --device-auth` (an address and a one-time code) on another machine or with
#           STUDIO_SIGNIN_DEVICE=1.
#   grok    `grok login --device-auth`: an address and a code, for headless and remote machines.
#   hermes  `hermes auth add <provider> --type oauth --no-browser`, one of HERMES_PROVIDERS.
#   gemini  signs in inside its own first interactive run: the page shows the Terminal command.
SIGNIN_TIMEOUT = 6 * 60
STATUS_TIMEOUT = 15
STATUS_TTL = 30
HERMES_PROVIDERS = ("openai-codex", "anthropic", "nous")
SIGNIN_NOTES = {
    "claude": "Opens claude.ai for your Claude subscription; paste the code it shows back here.",
    "codex": "Signs in with ChatGPT. On another computer it gives you a one-time code to enter at openai.com.",
    "grok": "Gives you a one-time code to enter at x.ai.",
    "hermes": "Adds a provider sign-in to Hermes' own credential pool.",
    "gemini": "Gemini CLI signs in with Google the first time it runs in a terminal.",
}
ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?<>=]*[ -/]*[@-~]|\x1b[()][A-Za-z0-9]|\x1b[=>78DEHMNOZc]|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
OSC_LINK = re.compile(r"\x1b\]8;[^;\x07\x1b]*;([^\x07\x1b]+)(?:\x07|\x1b\\)")
URL = re.compile(r"https?://[^\s\"'<>`\x07\x1b]+")
DEVICE_CODE = re.compile(r"(?<![A-Za-z0-9-])([A-Z0-9]{3,8}(?:-[A-Z0-9]{3,8}){1,3})(?![A-Za-z0-9-])")
CODE_PROMPT = re.compile(r"paste|enter (?:the |your )?(?:authori[sz]ation |verification )?code|code here|authentication code|code:", re.I)
SECRETISH = re.compile(r"\b(?=[A-Za-z0-9_\-+/=.]*[0-9])(?=[A-Za-z0-9_\-+/=.]*[A-Za-z])[A-Za-z0-9_\-+/=.]{24,}\b")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
signin: dict = {"backend": None}  # the one running sign-in, its process, pty and what it printed
statuses: dict = {}  # (backend, host) -> (monotonic time, answer)


def end_signin() -> None:
    """A sign-in still running when the Studio stops goes with it."""

    process = signin.get("process")
    if process is not None and process.poll() is None:
        stop(process)


atexit.register(end_signin)


def terminal_line(backend: str) -> str:
    """What to type in a terminal instead, shown on the card for every brain."""

    return {"claude": "claude auth login", "codex": "codex login", "grok": "grok login", "gemini": "gemini",
            "hermes": " ".join(["hermes", *profile_args(), "setup"])}.get(backend, "")


def recipe_of(backend: str, remote: bool, body: dict | None = None) -> dict:
    body = body or {}
    if backend == "claude":
        return {"flow": "pty", "args": ["auth", "login", "--claudeai"]}
    if backend == "codex":
        device = remote or os.environ.get("STUDIO_SIGNIN_DEVICE") == "1" or body.get("device") is True
        return {"flow": "pty", "args": ["login", "--device-auth"] if device else ["login"], "device": device}
    if backend == "grok":
        return {"flow": "pty", "args": ["login", "--device-auth"]}
    if backend == "hermes":
        provider = body.get("provider") or HERMES_PROVIDERS[0]
        if provider not in HERMES_PROVIDERS:
            raise HTTPException(400, "provider is one of " + ", ".join(HERMES_PROVIDERS))
        return {"flow": "pty", "args": [*profile_args(), "auth", "add", provider, "--type", "oauth", "--no-browser"]}
    return {"flow": "terminal", "args": []}


def plain(raw: str) -> str:
    return ANSI.sub("", raw.replace("\r\n", "\n").replace("\r", "\n"))


def sign_url(urls: list[str]) -> str | None:
    """The address a person opens: the provider's own page before a localhost callback, the first one printed."""

    urls = [u.rstrip(".,;:)]}") for u in urls]
    away = [u for u in urls if (urlsplit(u).hostname or "") not in ("localhost", "127.0.0.1", "::1", "0.0.0.0")]
    return (away or urls or [None])[0]


def device_code_in(text: str) -> str | None:
    lines = URL.sub(" ", text).splitlines()
    for i, line in enumerate(lines):
        near = " ".join(lines[max(0, i - 2):i + 1])
        if re.search(r"code", near, re.I):
            match = DEVICE_CODE.search(line)
            if match and re.search(r"[0-9]", match.group(1)) or match and len(match.group(1)) >= 8 and "-" in match.group(1):
                return match.group(1)
    return None


def redact(text: str, secrets=()) -> str:
    """What the log may show: addresses without their query, no codes, tokens or email addresses."""

    for secret in secrets:
        if secret:
            text = text.replace(secret, "[code]")
    text = URL.sub(lambda m: m.group(0).split("?")[0].split("#")[0] + ("?…" if "?" in m.group(0) else ""), text)
    text = DEVICE_CODE.sub(lambda m: "[code]" if re.search(r"[0-9]", m.group(1)) else m.group(1), text)
    text = SECRETISH.sub("[hidden]", text)
    return EMAIL.sub("[email]", text)


def read_signin(state: dict) -> dict:
    """The page's view of a sign-in from what its CLI printed so far."""

    raw = state.get("raw", "")
    text = plain(raw)
    url = sign_url(OSC_LINK.findall(raw) + URL.findall(text))
    secrets = [state.get("sent") or "", state.get("code") or ""]
    out = {"backend": state.get("backend"), "where": state.get("where") or "here", "started": state.get("started"),
           "command": state.get("command")}
    if state.get("ended"):
        out["state"] = state["ended"]
        if state.get("reason"):
            out["reason"] = state["reason"]
    elif state.get("sent"):
        out["state"] = "verifying"
    else:
        code = device_code_in(text) if url else None
        if code:
            state["code"] = code
            out.update(state="device_code", url=url, code=code)
        elif url and CODE_PROMPT.search(text[max(0, text.find(url.split("?")[0])):]):
            prompt = next((line.strip() for line in reversed(text.splitlines()) if CODE_PROMPT.search(line)), "Paste the code")
            out.update(state="needs_code", url=url, prompt=redact(prompt)[:120])
        elif url:
            out.update(state="needs_browser", url=url)
        else:
            out["state"] = "starting"
    if out["state"] in ("verifying", "done", "failed") and url:
        out["url"] = url
    lines = [redact(line.strip(), secrets)[:240] for line in text.splitlines() if line.strip()]
    out["lines"] = lines[-12:]
    return out


def signin_argv(backend: str, host: str, args: list[str]) -> list[str]:
    if host:
        name, _, port = host.partition(":")
        return [*SSH, "-tt", *(["-p", port] if port else []), name, "--", REMOTE_PATH + remote_line([remote_cli(host, backend), *args])]
    path = cli(backend)
    if not path:
        raise HTTPException(400, f"{CLIS[backend][0]} is not installed on this machine")
    return [path, *args]


def pump(state: dict, fd: int, process) -> None:
    """Read the pty until the CLI ends, the deadline passes or the page cancels."""

    import select
    deadline, eof = state["deadline"], False
    try:
        while True:
            if state.get("cancelled"):
                stop(process)
                finish(state, "failed", "Cancelled.")
                break
            if time.monotonic() > deadline:
                stop(process)
                finish(state, "failed", f"No sign-in after {SIGNIN_TIMEOUT // 60} minutes: start again when you are ready.")
                break
            ready, _, _ = select.select([fd], [], [], 0.25)
            if ready:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:  # the pty's other end is closed: the CLI has ended or is ending
                    chunk = b""
                if chunk:
                    with lock:
                        state["raw"] = (state.get("raw", "") + chunk.decode("utf-8", "replace"))[-65536:]
                    continue
                eof = True
            if process.poll() is None:
                if eof:
                    time.sleep(0.1)
                continue
            code = process.returncode
            if code == 0:
                finish(state, "done", None)
            else:
                text = [redact(line.strip(), [state.get("sent") or ""]) for line in plain(state.get("raw", "")).splitlines() if line.strip()]
                where = f"on {state['where'][4:]}" if state.get("where", "here") != "here" else "on this machine"
                finish(state, "failed", (text[-1][:240] if text else f"{CLIS[state['backend']][0]} stopped") +
                       f" (exit {code} {where})" + (" · ssh could not reach the computer" if code == 255 and state.get("where") != "here" else ""))
            break
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def finish(state: dict, ended: str, reason: str | None) -> None:
    with lock:
        if not state.get("ended"):
            state.update(ended=ended, reason=reason, process=None)
        probes.clear()
        statuses.clear()


def start_signin(backend: str, body: dict) -> dict:
    import pty  # noqa: F401  (POSIX only; the import fails loudly on a machine without ptys)
    import termios
    import fcntl
    import struct
    if backend not in CLIS:
        raise HTTPException(404, "no such brain")
    host = where_of(backend)
    recipe = recipe_of(backend, bool(host), body)
    if recipe["flow"] != "pty":
        raise HTTPException(400, f"{CLIS[backend][0]} signs in inside its own terminal: run `{terminal_line(backend)}` there")
    with lock:
        if signin.get("process") is not None and not signin.get("ended"):
            raise HTTPException(409, f"{CLIS[signin['backend']][0]} is signing in: finish or cancel that first")
    try:
        argv = signin_argv(backend, host, recipe["args"])
    except RuntimeError as error:
        raise HTTPException(400, str(error))
    master, slave = os.openpty()
    attrs = termios.tcgetattr(slave)
    attrs[3] &= ~termios.ECHO  # a pasted code is not printed back
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 60, 1000, 0, 0))  # wide enough that an address never wraps
    env = child_env({"TERM": "dumb", "NO_COLOR": "1", "COLUMNS": "1000", "LINES": "60"})
    try:
        process = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True,
                                   cwd=str(HOME) if HOME else None, close_fds=True)
    except OSError as error:
        os.close(master)
        os.close(slave)
        raise HTTPException(400, f"could not start {CLIS[backend][0]}: {error}")
    os.close(slave)
    state = {"backend": backend, "where": f"ssh:{host}" if host else "here", "process": process, "fd": master, "raw": "",
             "started": time.time(), "deadline": time.monotonic() + SIGNIN_TIMEOUT, "command": " ".join([CLIS[backend][1], *recipe["args"]])}
    with lock:
        signin.clear()
        signin.update(state)
        state = signin
    threading.Thread(target=pump, args=(state, master, process), daemon=True).start()
    return read_signin(state)


def signin_of(backend: str) -> dict:
    with lock:
        if signin.get("backend") != backend:
            return {"backend": backend, "state": "idle", "lines": []}
        return read_signin(signin)


def send_code(backend: str, body: dict) -> dict:
    code = body.get("code")
    if not isinstance(code, str) or not code.strip() or len(code) > 2048 or any(c in code for c in "\r\n\0\x1b"):
        raise HTTPException(400, "code is the text the sign-in page showed, on one line")
    with lock:
        if signin.get("backend") != backend or signin.get("ended") or signin.get("process") is None:
            raise HTTPException(409, "no sign-in is waiting for a code")
        fd = signin["fd"]
        signin["sent"] = code.strip()
    try:
        os.write(fd, (code.strip() + "\r").encode())
    except OSError:
        raise HTTPException(409, "the sign-in has already ended")
    return signin_of(backend)


def cancel_signin(backend: str) -> dict:
    with lock:
        if signin.get("backend") == backend and not signin.get("ended"):
            signin["cancelled"] = True
            process = signin.get("process")
        else:
            process = None
    if process is not None:
        for _ in range(40):  # the reader stops it within a quarter of a second
            if signin.get("ended"):
                break
            time.sleep(0.1)
    return signin_of(backend)


def status_args(backend: str) -> list[str]:
    return {"claude": ["auth", "status", "--json"], "codex": ["login", "status"], "grok": ["models"],
            "hermes": [*profile_args(), "auth", "list"]}.get(backend, [])


def run_short(argv_tail: list[str], backend: str, host: str, timeout: int = STATUS_TIMEOUT) -> subprocess.CompletedProcess:
    if host:
        line = REMOTE_PATH + remote_line([remote_cli(host, backend), *argv_tail]) + " </dev/null"
        return subprocess.run([*ssh_of(host), line], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    path = cli(backend)
    if not path:
        raise RuntimeError(f"{CLIS[backend][0]} is not installed on this machine")
    return subprocess.run([path, *argv_tail], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                          env=child_env(), cwd=str(HOME) if HOME else None)


def read_status(backend: str, done: subprocess.CompletedProcess) -> dict:
    said = plain((done.stdout or "") + "\n" + (done.stderr or ""))
    if backend == "claude":
        try:
            info = json.loads(done.stdout)
        except ValueError:
            return {"state": "unknown", "detail": redact(said.strip()[-160:])}
        how = {"claude.ai": "Claude subscription", "oauth_token": "Claude subscription", "console": "Anthropic Console"}.get(
            str(info.get("authMethod") or ""), str(info.get("authMethod") or ""))
        plan = str(info.get("subscriptionType") or "")
        return {"state": "signed_in", "detail": " · ".join(x for x in (how, plan) if x)} if info.get("loggedIn") else \
            {"state": "signed_out", "detail": "Not signed in"}
    if backend == "codex":
        if re.search(r"not logged in", said, re.I):
            return {"state": "signed_out", "detail": "Not signed in"}
        found = re.search(r"logged in using ([^\n]+)", said, re.I)
        if found:
            return {"state": "signed_in", "detail": redact(found.group(1).strip())[:60]}
    if backend == "grok":
        found = re.search(r"logged in with ([^\s\n]+)", said, re.I)
        if found:
            return {"state": "signed_in", "detail": redact(found.group(1).rstrip("."))[:60]}
        if re.search(r"not logged in|not signed in|grok login|unauthori[sz]ed|401", said, re.I):
            return {"state": "signed_out", "detail": "Not signed in"}
    if backend == "hermes":
        pools = re.findall(r"^([a-z0-9][a-z0-9_.-]*) \((\d+) credentials?\)", said, re.M)
        names = [name for name, count in pools if int(count)]
        return {"state": "signed_in", "detail": "Providers: " + ", ".join(names)} if names else {"state": "signed_out", "detail": "No provider signed in"}
    tail = [line for line in said.splitlines() if line.strip()]
    return {"state": "unknown", "detail": redact(tail[-1].strip())[:160] if tail else "No answer"}


def gemini_status(host: str) -> dict:
    look = 'test -f "$HOME/.gemini/oauth_creds.json" && echo yes || echo no'
    try:
        done = subprocess.run([*ssh_of(host), look] if host else ["sh", "-c", look], capture_output=True, text=True,
                              timeout=PROBE_TIMEOUT + 2, stdin=subprocess.DEVNULL, env=None if host else child_env())
    except (OSError, subprocess.TimeoutExpired):
        return {"state": "unknown", "detail": "Unknown until its first use"}
    return {"state": "signed_in", "detail": "Google"} if done.stdout.strip() == "yes" else \
        {"state": "unknown", "detail": "Unknown until its first use: Gemini CLI signs in when it first runs"}


def status_of(backend: str) -> dict:
    """The CLI's own word on its sign-in (kept STATUS_TTL seconds): {state: signed_in|signed_out|unknown, detail}."""

    if backend not in CLIS:
        raise HTTPException(404, "no such brain")
    host = where_of(backend)
    with lock:
        cached = statuses.get((backend, host))
    if cached and time.monotonic() - cached[0] < STATUS_TTL:
        return cached[1]
    where = {"where": f"ssh:{host}" if host else "here"}
    try:
        answer = gemini_status(host) if backend == "gemini" else read_status(backend, run_short(status_args(backend), backend, host))
    except subprocess.TimeoutExpired:
        answer = {"state": "unknown", "detail": f"No answer in {STATUS_TIMEOUT} seconds"}
    except (OSError, RuntimeError) as error:
        answer = {"state": "missing", "detail": str(error)[:200]}
    answer = {"backend": backend, **where, **answer}
    with lock:
        statuses[(backend, host)] = (time.monotonic(), answer)
    return answer


def sign_out(backend: str, body: dict | None = None) -> dict:
    if backend not in CLIS:
        raise HTTPException(404, "no such brain")
    args = {"claude": ["auth", "logout"], "codex": ["logout"], "grok": ["logout"]}.get(backend)
    if backend == "hermes":
        provider = (body or {}).get("provider")
        if provider not in HERMES_PROVIDERS:
            raise HTTPException(400, "Hermes signs out one provider at a time: provider is one of " + ", ".join(HERMES_PROVIDERS))
        args = [*profile_args(), "auth", "logout", provider]
    if not args:
        raise HTTPException(400, f"{CLIS[backend][0]} signs out inside its own terminal")
    try:
        done = run_short(args, backend, where_of(backend))
    except subprocess.TimeoutExpired:
        raise HTTPException(504, f"{CLIS[backend][0]} did not sign out in {STATUS_TIMEOUT} seconds")
    except (OSError, RuntimeError) as error:
        raise HTTPException(400, str(error)[:200])
    with lock:
        statuses.clear()
        probes.clear()
    said = [redact(line.strip()) for line in plain(done.stdout + "\n" + done.stderr).splitlines() if line.strip()]
    return {"ok": done.returncode == 0, "lines": said[-6:], **status_of(backend)}


# ---------- the routes ----------

def local(request: Request, mutation: bool = False):
    # Brains and agents run programs with the user's sign-ins: this computer or its Tailscale address only (as updates.py).
    host = request.url.hostname or ""
    if not request.client or request.client.host not in ("127.0.0.1", "::1", "testclient") or not (
            host in ("localhost", "127.0.0.1", "::1", "testserver") or host.endswith(".ts.net")):
        raise HTTPException(403, "Set up brains and agents on this computer or through its Tailscale address.")
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (origin and urlsplit(origin).netloc != request.headers.get("host")):
        raise HTTPException(403, "Open the Studio page to use brains and agents.")
    if mutation and request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(415, "Send this from the Studio page.")


def attach(home: Path) -> APIRouter:
    global HOME
    HOME = home
    (HOME / "agent").mkdir(parents=True, exist_ok=True)
    with lock:
        tasks.clear()
        threads.clear()
        while not pending.empty():
            pending.get_nowait()
        load_tasks()
        load_threads()
    if not pending.empty():
        ensure_worker()
    router = APIRouter()

    @router.get("/api/brains")
    def brains(request: Request, endpoint: str = ""):
        local(request)
        config = saved_settings()
        return {"backends": detect(config, endpoint), "settings": config, "default": config["default"]}

    @router.post("/api/brains/settings")
    def brain_settings(body: dict, request: Request):
        local(request, True)
        return {"settings": save_settings(body)}

    @router.post("/api/brains/test")
    def brain_test(body: dict, request: Request):
        local(request, True)
        started = time.time()
        backend = body.get("backend") or saved_settings()["default"]
        if backend not in BACKENDS:
            raise HTTPException(400, "backend is one of " + ", ".join(BACKENDS))
        try:
            text = ask(backend, "", "Reply with the single word: ready", "fast" if backend == "claude" else "top", timeout=TEST_TIMEOUT)
            out = {"ok": bool(text.strip()), "text": text, "error": None}
        except subprocess.TimeoutExpired:
            out = {"ok": False, "text": "", "error": f"no answer in {TEST_TIMEOUT // 60} minutes"}
        except Exception as error:  # the reason goes to the page as it is
            out = {"ok": False, "text": "", "error": str(error)[:600]}
        return {**out, "backend": backend, "elapsedMs": int(1000 * (time.time() - started))}

    # signing a brain in with the CLI's own login (start, follow, a pasted code, cancel), its status and signing out
    @router.get("/api/brains/{backend}/status")
    def brain_status(backend: str, request: Request):
        local(request)
        return status_of(backend)

    @router.post("/api/brains/{backend}/signin")
    def brain_signin(backend: str, body: dict, request: Request):
        local(request, True)
        return start_signin(backend, body)

    @router.get("/api/brains/{backend}/signin")
    def brain_signin_state(backend: str, request: Request):
        local(request)
        return signin_of(backend)

    @router.post("/api/brains/{backend}/signin/code")
    def brain_signin_code(backend: str, body: dict, request: Request):
        local(request, True)
        return send_code(backend, body)

    @router.post("/api/brains/{backend}/signin/cancel")
    def brain_signin_cancel(backend: str, body: dict, request: Request):
        local(request, True)
        return cancel_signin(backend)

    @router.post("/api/brains/{backend}/signout")
    def brain_signout(backend: str, body: dict, request: Request):
        local(request, True)
        return sign_out(backend, body)

    @router.get("/api/agent/tools")
    def list_tools(request: Request):
        local(request)
        return {"tools": tools()}

    @router.post("/api/agent/tools")
    def change_tools(body: dict, request: Request):
        local(request, True)
        return {"tools": save_tools(body)}

    @router.post("/api/agent/tools/blender/start")
    def blender_start(request: Request):
        local(request, True)
        return start_blender()

    @router.post("/api/agent/tools/{tool_id}/check")
    def tool_check(tool_id: str, request: Request):
        local(request, True)
        return {"id": tool_id, **check_tool(tool_of(tool_id))}

    @router.post("/api/agent/tasks")
    def new_task(body: dict, request: Request):
        local(request, True)
        return submit(body)

    @router.get("/api/agent/tasks")
    def list_tasks(request: Request):
        local(request)
        return {"tasks": [public(t, log=False) for t in sorted(tasks.values(), key=lambda t: -t["created"])]}

    @router.get("/api/agent/tasks/{task_id}")
    def one_task(task_id: str, request: Request):
        local(request)
        if task_id not in tasks:
            raise HTTPException(404, "no such task")
        return public(tasks[task_id])

    @router.post("/api/agent/tasks/{task_id}/cancel")
    def cancel_task(task_id: str, request: Request):
        local(request, True)
        return cancel(task_id)

    # the Agent room: conversations whose turns are tasks (POST /api/agent/tasks with a `thread` continues one too)
    @router.get("/api/agent/threads")
    def list_threads(request: Request):
        local(request)
        return {"threads": [summary(t) for t in sorted(threads.values(), key=lambda t: -t["updated"])]}

    @router.post("/api/agent/threads")
    def start_thread(body: dict, request: Request):
        local(request, True)
        if body.get("thread"):
            raise HTTPException(400, "a new conversation has no thread yet: post to /api/agent/threads/{id}/messages")
        task = submit(body)
        return {"thread": thread_public(threads[task["thread"]]), "task": task}

    @router.get("/api/agent/threads/{thread_id}")
    def one_thread(thread_id: str, request: Request):
        local(request)
        return thread_public(thread_of(thread_id))

    @router.post("/api/agent/threads/{thread_id}")
    def change_thread(thread_id: str, body: dict, request: Request):
        local(request, True)
        return rename_thread(thread_id, body)

    @router.delete("/api/agent/threads/{thread_id}")
    def remove_thread(thread_id: str, request: Request):
        local(request)
        if request.headers.get("sec-fetch-site") not in (None, "same-origin"):
            raise HTTPException(403, "Open the Studio page to use brains and agents.")
        return forget_thread(thread_id)

    @router.post("/api/agent/threads/{thread_id}/messages")
    def reply(thread_id: str, body: dict, request: Request):
        local(request, True)
        thread_of(thread_id)
        task = submit({**body, "thread": thread_id})
        return {"thread": thread_public(threads[thread_id]), "task": task}

    @router.post("/api/agent/threads/{thread_id}/cancel")
    def stop_thread(thread_id: str, request: Request):
        local(request, True)
        thread = thread_of(thread_id)
        if thread.get("pending"):
            cancel(thread["pending"])
        return thread_public(thread)

    return router
