"""Brains and agents: the user's own subscriptions wired into the Studio.

A brain is a coding CLI the user is already signed in to (Claude Code, Codex with a ChatGPT sign-in, Gemini CLI,
Grok CLI) or a local OpenAI-compatible server. No API keys: each CLI uses its own sign-in, in print mode. A CLI may
run here or on another machine over ssh (a DGX Spark often has none installed, the Mac beside it does): the prompt
goes on stdin, the arguments are quoted for the remote shell, and nothing is left behind there.

"Ask your agent" runs Claude Code or Codex once, non-interactively, with MCP tools (Blender, Blockout, this Studio)
in a working folder of its own, $STUDIO_HOME/agent/<id>, one task at a time. What it saves there is listed as the
task's outputs and served by the Studio's /files route.

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
MAC_CODEX = ("/Applications/ChatGPT.app/Contents/Resources/codex", "/Applications/Codex.app/Contents/Resources/codex")
HOST = re.compile(r"(?:[A-Za-z0-9._-]{1,64}@)?[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252})(?::[0-9]{1,5})?")
TOOL_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
ENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")

# id -> (label, command, how to sign in)
CLIS = {
    "claude": ("Claude Code", "claude", "Run `claude auth login` in a terminal on that machine (your Claude subscription)."),
    "codex": ("Codex · ChatGPT sign-in", "codex", "Run `codex login` in a terminal on that machine and choose Sign in with ChatGPT."),
    "gemini": ("Gemini CLI", "gemini", "Run `gemini` once in a terminal on that machine and sign in with Google."),
    "grok": ("Grok CLI", "grok", "Run `grok login` in a terminal on that machine."),
}
BACKENDS = (*CLIS, "local")
HARNESSES = ("claude", "codex")
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
}
SYSTEM = ("You are working for a filmmaker inside Slate Studio. Save every deliverable (renders as .png, .jpg or .mp4, "
          "models as .glb or .blend, data as .json) into the current folder{folder}. When a tool saves a file itself, give it "
          "that folder's absolute path.{studio} Finish with a short plain summary that lists the files you made.")

lock = threading.RLock()
probes: dict = {}  # (host or "", cli) -> (monotonic time, found)
tasks: dict[str, dict] = {}
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


def cli(name: str) -> str | None:
    found = shutil.which(CLIS[name][1] if name in CLIS else name, path=search_path())
    if not found and name == "codex" and sys.platform == "darwin":
        found = next((p for p in MAC_CODEX if os.access(p, os.X_OK)), None)
    return found


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
        extra = " || ls " + " ".join(MAC_CODEX) + " 2>/dev/null | head -1" if name == "codex" else ""
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
                "path": None, "version": None, "signin": signin}
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
        out.append(item)
    base, models = seen.get(("local", "")) or (None, [])
    out.append({"id": "local", "label": "Local model server", "kind": "http", "found": bool(base),
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
                                    "finished", "reply", "outputs", "error")}
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


def submit(body: dict) -> dict:
    prompt = str(body.get("prompt") or "").strip()
    if not prompt or len(prompt) > 20000:
        raise HTTPException(400, "a prompt is needed (up to 20,000 characters)")
    backend = body.get("backend") or (saved_settings()["default"] if saved_settings()["default"] in HARNESSES else "claude")
    if backend not in HARNESSES:
        raise HTTPException(400, "an agent task runs on claude or codex")
    known = {t["id"]: t for t in tools()}
    chosen = body.get("tools") if body.get("tools") is not None else [t for t, v in known.items() if v["enabled"]]
    if not isinstance(chosen, list) or any(t not in known for t in chosen):
        raise HTTPException(400, "tools are ids from /api/agent/tools")
    if any(not known[t]["enabled"] for t in chosen):
        raise HTTPException(400, "a chosen tool is turned off")
    attach_ = body.get("attach") or []
    if not isinstance(attach_, list) or len(attach_) > 20:
        raise HTTPException(400, "attach is a list of up to 20 Library paths")
    for path in attach_:
        resolved = (HOME / str(path)).resolve()
        if HOME.resolve() not in resolved.parents or not resolved.is_file():
            raise HTTPException(400, f"{path} is not a file in the studio folder")
    task_id = time.strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    host = where_of(backend)
    task = {"id": task_id, "status": "queued", "prompt": prompt, "backend": backend, "tools": list(dict.fromkeys(chosen)),
            "attach": [str(p) for p in attach_], "where": f"ssh:{host}" if host else "here", "created": time.time(),
            "started": None, "finished": None, "reply": "", "outputs": [], "error": None, "inputs": []}
    task_folder(task_id).mkdir(parents=True, exist_ok=True)
    (task_folder(task_id) / "log.txt").write_text("")
    with lock:
        tasks[task_id] = task
        save_task(task)
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


def harness_args(task: dict, tool: str, config_path: str, servers: dict, system: str) -> list[str]:
    if task["backend"] == "claude":
        allowed = [f"mcp__{name}" for name in servers] + ["Read", "Write", "Glob"]
        return [tool, "-p", "--output-format", "stream-json", "--verbose", "--mcp-config", config_path, "--strict-mcp-config",
                "--tools", "Read,Write,Glob", "--allowedTools", ",".join(allowed), "--permission-mode", "acceptEdits",
                "--append-system-prompt", system]
    args = [tool, "exec", "--skip-git-repo-check", "--sandbox", "workspace-write", "--json"]
    for name, server in servers.items():
        for key in ("command", "args", "env"):
            if key in server:
                args += ["-c", f"mcp_servers.{name}.{key}={toml_value(server[key])}"]
    return args + ["-"]


def render(backend: str, line: str, task: dict) -> str:
    """One line of the harness's stream as a person reads it."""

    try:
        event = json.loads(line)
    except ValueError:
        return line.rstrip("\n") + "\n" if line.strip() else ""
    if not isinstance(event, dict):
        return ""
    out = []
    if backend == "claude":
        if event.get("type") == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and block.get("text", "").strip():
                    out.append(block["text"].strip())
                elif block.get("type") == "tool_use":
                    out.append("→ " + str(block.get("name")))
        elif event.get("type") == "result":
            if isinstance(event.get("result"), str):
                task["reply"] = event["result"]
            if event.get("is_error"):
                task["_error"] = hinted("claude", str(event.get("result") or "Claude Code returned an error"), task["_host"])
        elif event.get("type") == "system" and event.get("subtype") == "init":
            ready = [s.get("name") + (" (" + s.get("status") + ")" if s.get("status") != "connected" else "") for s in event.get("mcp_servers") or []]
            out.append("tools: " + (", ".join(ready) or "none"))
    else:
        _, reply, error = codex_events(line)
        item = event.get("item") or {}
        if reply:
            task["reply"] = reply
            out.append(reply.strip())
        elif error:
            task["_error"] = hinted("codex", error, task["_host"])
            out.append("error: " + error)
        elif event.get("type") == "item.started" and item.get("type") == "mcp_tool_call":
            out.append(f"→ {item.get('server')}.{item.get('tool')}")
        elif event.get("type") == "item.started" and item.get("type") == "command_execution":
            out.append("$ " + str(item.get("command"))[:300])
    return "".join(text + "\n" for text in out)


def perform(task: dict) -> None:
    config = saved_settings()
    host = where_of(task["backend"], config)
    task.update(status="running", started=time.time(), _host=host)
    save_task(task)
    here = task_folder(task["id"])
    log = open(here / "log.txt", "a")
    try:
        names = []
        for path in task["attach"]:  # the attachments, beside what the agent makes
            source = (HOME / path).resolve()
            name = source.name if source.name not in names else f"{len(names)}_{source.name}"
            try:
                os.link(source, here / name)
            except OSError:
                shutil.copy2(source, here / name)
            names.append(name)
        task["inputs"] = names
        servers = mcp_servers(task, host, config)
        write_json(here / "mcp.json", {"mcpServers": servers})
        studio = "" if not host else f" Tools that run on the Studio's own machine (Blender, the Studio) save into {here} there."
        if host:
            tool = remote_cli(host, task["backend"])
            made = subprocess.run([*ssh_of(host), "mkdir -p ~/.slate-studio-agent && mktemp -d ~/.slate-studio-agent/" + task["id"] + ".XXXXXX"],
                                  capture_output=True, text=True, timeout=PROBE_TIMEOUT + 4, stdin=subprocess.DEVNULL)
            where = made.stdout.strip()
            if made.returncode or not where.startswith("/"):
                raise RuntimeError(f"could not make a working folder on {host}: " + (made.stderr.strip()[-300:] or "no answer"))
            port, name = scp_target(host)
            sent = subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", *port,
                                   *[str(here / n) for n in ["mcp.json", *names]], f"{name}:{where}/"],
                                  capture_output=True, text=True, timeout=600)
            if sent.returncode:
                raise RuntimeError(f"could not copy the task's files to {host}: " + sent.stderr.strip()[-300:])
            system = SYSTEM.format(folder=f" ({where} on {host})", studio=studio)
            args = harness_args(task, tool, f"{where}/mcp.json", servers, system)
            line = REMOTE_PATH + f"cd {shlex.quote(where)} && echo $$ > .pid && exec " + remote_line(args)
            argv, cwd = [*ssh_of(host), line], None
            with lock:
                running["remote"] = (host, where)
        else:
            tool = cli(task["backend"])
            if not tool:
                raise RuntimeError(f"{CLIS[task['backend']][0]} is not installed on this machine: install it and sign in, or run it over ssh")
            system = SYSTEM.format(folder=f" ({here})", studio="")
            argv, cwd = harness_args(task, tool, str(here / "mcp.json"), servers, system), str(here)
        log.write(f"[{task['backend']} {'on ' + host if host else 'here'}] {task['prompt'][:200]}\n")
        log.flush()
        with lock:
            if task["status"] == "cancelled":
                return
            process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(here / "stderr.txt", "w"),
                                       text=True, cwd=cwd, env=child_env(), start_new_session=True)
            running.update(id=task["id"], process=process)
        # Claude Code takes the instruction as its system prompt; Codex has no such flag and reads it ahead of the task
        process.stdin.write(task["prompt"] if task["backend"] == "claude" else f"{system}\n\n---\n\n{task['prompt']}")
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
        if host:  # what the agent made, back beside the task's record; nothing here is removed
            shell = " ".join(ssh_of(host)[:-2])  # ssh with its options and port, as rsync runs it
            back = subprocess.run(["rsync", "-a", "--exclude", ".pid", "-e", shell, f"{name}:{where}/", f"{here}/"]
                                  if shutil.which("rsync") else ["scp", "-q", "-r", "-o", "BatchMode=yes", *port, f"{name}:{where}/.", str(here)],
                                  capture_output=True, text=True, timeout=1800)
            if back.returncode:
                log.write(f"could not copy the outputs back from {host}: {back.stderr.strip()[-300:]}\n")
        if task["status"] == "cancelled":
            return
        error = task.pop("_error", None)
        if process.returncode or error:
            said = (here / "stderr.txt").read_text(errors="replace").strip()
            task.update(status="failed", error=error or hinted(task["backend"], said or f"{task['backend']} stopped with code {process.returncode}", host))
        else:
            task["status"] = "done"
    finally:
        log.close()
        task.pop("_host", None)
        task.pop("_error", None)


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
        while not pending.empty():
            pending.get_nowait()
        load_tasks()
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

    return router
