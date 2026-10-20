"""The Agent room's conversations (app/agents.py threads): python3 -m pytest tests/test_agents_threads.py. The CLIs are the
stand-ins of tests/test_agents.py, which log every call: no sign-ins, no network."""
import json
import sys
import time
from pathlib import Path

import pytest

from test_agents import agents, client, fake, post, wait_for  # noqa: F401  (fake is the fixture)


def calls_of(fake, cli: str) -> list[dict]:
    return [c for c in fake["calls"]() if c.get("cli") == cli]


def settled(thread_id: str, seconds: float = 20) -> dict:
    ends = time.time() + seconds
    while time.time() < ends:
        thread = client.get(f"/api/agent/threads/{thread_id}").json()
        if not thread["live"]:
            return thread
        time.sleep(0.1)
    raise AssertionError(thread)


def start(text: str, backend: str, **more) -> dict:
    answer = post("/api/agent/threads", {"text": text, "backend": backend, "tools": [], **more})
    assert answer.status_code == 200, answer.text
    return answer.json()


def say(thread_id: str, text: str, **more) -> dict:
    answer = post(f"/api/agent/threads/{thread_id}/messages", {"text": text, **more})
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_a_thread_is_stored_with_its_turns_and_read_back(fake):
    made = start("Build a diner booth", "claude")
    thread = made["thread"]
    assert thread["id"].startswith("th-") and thread["title"] == "Build a diner booth" and thread["status"] in ("queued", "running")
    done = settled(thread["id"])
    roles = [t["role"] for t in done["turns"]]
    assert roles[0] == "user" and roles[-1] == "agent" and "tool" in roles
    user, agent = done["turns"][0], done["turns"][-1]
    assert user["text"] == "Build a diner booth" and user["attachments"] == [] and user["at"] > 0
    assert agent["text"] == "Made render.png" and agent["status"] == "done" and agent["question"] is False
    assert agent["attachments"] == [f"agent/{thread['id']}/render.png"] and agent["jobs"] == ["job1"]
    tool_lines = [t["text"] for t in done["turns"] if t["role"] == "tool"]
    assert "→ fake · make" in tool_lines and "video queued: agent: clip" in tool_lines
    assert done["session"] is True and done["status"] == "idle" and done["waiting"] is False
    # the file holds the harness's session id, never anything else of the sign-in
    path, until = agents.HOME / ".studio-agents/threads" / f"{thread['id']}.json", time.time() + 10
    record = json.loads(path.read_text())
    while record.get("session") is None and time.time() < until:  # the file is written a moment after the turn ends
        time.sleep(0.05)
        record = json.loads(path.read_text())
    assert set(record["session"]) == {"backend", "id", "where"} and record["session"]["backend"] == "claude"
    assert set(record) >= {"id", "title", "backend", "tools", "created", "updated", "turns", "pending"}
    listed = client.get("/api/agent/threads").json()["threads"]
    assert listed[0]["id"] == thread["id"] and listed[0]["turns"] == 2 and listed[0]["delivered"] == 1
    # read back after a restart
    agents.threads.clear()
    agents.load_threads()
    assert client.get(f"/api/agent/threads/{thread['id']}").json()["turns"] == done["turns"]
    # renamed, then forgotten: what it made stays
    assert post(f"/api/agent/threads/{thread['id']}", {"title": "Diner"}).json()["title"] == "Diner"
    assert post(f"/api/agent/threads/{thread['id']}", {"title": "  "}).status_code == 400
    assert client.delete(f"/api/agent/threads/{thread['id']}").json()["removed"]
    assert client.get(f"/api/agent/threads/{thread['id']}").status_code == 404
    assert (agents.HOME / "agent" / thread["id"] / "render.png").is_file()


def test_claude_code_names_its_session_then_resumes_it(fake):
    thread = start("Build a set", "claude")["thread"]
    settled(thread["id"])
    first = calls_of(fake, "claude")[-1]
    session = first["args"][first["args"].index("--session-id") + 1]
    assert "--resume" not in first["args"] and first["stdin"] == "Build a set"
    say(thread["id"], "Now light it warmer")
    settled(thread["id"])
    second = calls_of(fake, "claude")[-1]
    assert second["args"][second["args"].index("--resume") + 1] == session and "--session-id" not in second["args"]
    assert second["stdin"] == "Now light it warmer"  # the session holds the rest
    assert Path(second["cwd"]).resolve() == Path(first["cwd"]).resolve() == (agents.HOME / "agent" / thread["id"]).resolve()
    system = second["args"][second["args"].index("--append-system-prompt") + 1]
    assert "[question]" in system and "one clear question" in system and "Library paths" in system


def test_a_lost_session_starts_again_with_the_conversation_so_far(fake, monkeypatch):
    thread = start("Build a set", "claude")["thread"]
    settled(thread["id"])
    monkeypatch.setenv("FAKE_MODE", "lost")
    say(thread["id"], "And a second camera")
    done = settled(thread["id"])
    assert done["turns"][-1]["status"] == "done"
    retry = calls_of(fake, "claude")[-1]
    assert "--session-id" in retry["args"] and "The conversation so far" in retry["stdin"] and "Build a set" in retry["stdin"]
    assert retry["stdin"].endswith("And a second camera")


def test_codex_resumes_its_thread(fake):
    thread = start("Lay out the scene", "codex")["thread"]
    settled(thread["id"])
    first = calls_of(fake, "codex")[-1]
    assert first["args"][:2] == ["exec", "--skip-git-repo-check"] and "resume" not in first["args"]
    assert first["stdin"].startswith("You are working for a filmmaker inside Imagination Studio.")
    say(thread["id"], "Make it night")
    done = settled(thread["id"])
    second = calls_of(fake, "codex")[-1]
    assert second["args"][:2] == ["exec", "resume"] and second["args"][-2:] == ["codex-thread-1", "-"]
    assert 'sandbox_mode="workspace-write"' in second["args"] and "--sandbox" not in second["args"]
    assert second["stdin"] == "Make it night"
    assert done["turns"][-1]["jobs"] == ["job2"]
    assert "scout done → s1.png" in [t["text"] for t in done["turns"] if t["role"] == "tool"]


def test_hermes_continues_its_named_session_with_the_profile_from_settings(fake):
    (agents.HOME / "settings.json").write_text(json.dumps({"hermes_profile": "studio"}))
    thread = start("Plan a trailer", "hermes")["thread"]
    assert thread["tools"] == []  # a conversation: no tools go to Hermes
    done = settled(thread["id"])
    first = calls_of(fake, "hermes")[-1]
    assert first["args"][:2] == ["-p", "studio"] and first["args"][2:7] == ["chat", "--query-file", "-", "--format", "stream-json"]
    assert first["args"][-3:] == ["--continue", "imagination-" + thread["id"], "--create-if-missing"]
    assert first["args"][7:11] == ["--toolsets", "clarify,todo", "--max-turns", "25"]  # no terminal, browser or computer use
    assert "Imagination Studio" in first["stdin"] and "not connected in this conversation" in first["stdin"] and first["stdin"].endswith("Plan a trailer")
    assert done["turns"][-1]["text"] == "Here is a plan." and done["turns"][-1]["status"] == "done"
    say(thread["id"], "Shorter")
    settled(thread["id"])
    second = calls_of(fake, "hermes")[-1]
    assert second["args"][-2:] == ["--resume", "20261018_120000_abcdef"] and second["stdin"] == "Shorter"
    # a profile that is not a plain name is not passed on
    (agents.HOME / "settings.json").write_text(json.dumps({"hermes_profile": "x; rm -rf ~"}))
    assert agents.profile_args() == []


def test_hermes_as_a_brain(fake, monkeypatch):
    assert agents.ask("hermes", "Be terse.", "Reply with the single word: ready") == "ready"
    call = calls_of(fake, "hermes")[-1]
    assert call["args"][0] == "-z" and call["args"][1].startswith("Be terse.") and call["args"][1].endswith("ready")
    long_prompt = "ready " + "x" * 120000
    assert agents.ask("hermes", "", long_prompt) == "ready"
    assert calls_of(fake, "hermes")[-1]["args"][:3] == ["chat", "--query-file", "-"]
    assert call["args"][2:4] == ["-t", "todo"]  # a brain's answer: no Hermes toolset it could act with
    monkeypatch.setenv("FAKE_MODE", "hermes-nologin")
    thread = start("Plan", "hermes")["thread"]
    failed = settled(thread["id"])["turns"][-1]
    assert failed["status"] == "failed" and "hermes setup" in failed["error"]
    # over ssh, like the other CLIs
    monkeypatch.delenv("FAKE_MODE")
    post("/api/brains/settings", {"remote": {"host": "user@workstation", "backends": ["hermes"]}})
    found = {b["id"]: b for b in client.get("/api/brains").json()["backends"]}
    assert found["hermes"]["where"] == "ssh:user@workstation" and found["hermes"]["found"]
    assert agents.ask("hermes", "", "ready") == "ready" and any(c.get("ssh") == "user@workstation" for c in fake["calls"]())


def test_gemini_reads_the_recent_turns_ahead_of_the_message(fake, monkeypatch):
    seen = []
    monkeypatch.setattr(agents, "ask", lambda backend, system, prompt, *a, **k: seen.append((backend, system, prompt)) or "Two shots, then a reveal.")
    thread = start("A teaser for a horror film", "gemini")["thread"]
    settled(thread["id"])
    say(thread["id"], "Make the reveal a door")
    done = settled(thread["id"])
    backend, system, prompt = seen[-1]
    assert backend == "gemini" and "cannot run the Studio's tools" in system and "[question]" in system
    assert prompt.startswith("The conversation so far") and "A teaser for a horror film" in prompt and "Two shots, then a reveal." in prompt
    assert prompt.endswith("Make the reveal a door") and seen[0][2] == "A teaser for a horror film"
    assert [t["role"] for t in done["turns"]] == ["user", "agent", "user", "agent"]


def test_a_question_waits_for_the_reply(fake):
    thread = start("Make a clip, ask me how long", "claude")["thread"]
    done = settled(thread["id"])
    agent = done["turns"][-1]
    assert agent["question"] is True and agent["text"].endswith("How long should the clip be?") and "[question]" not in agent["text"]
    assert agent["options"] == ["5 s", "10 s"] and done["waiting"] is True
    assert client.get("/api/agent/threads").json()["threads"][0]["waiting"] is True
    say(thread["id"], "10 s")
    again = settled(thread["id"])
    assert again["waiting"] is False and [t["text"] for t in again["turns"] if t["role"] == "user"] == ["Make a clip, ask me how long", "10 s"]


@pytest.mark.parametrize("text, asked, shown, options", [
    ("Done: renders/a.mp4", False, "Done: renders/a.mp4", []),
    ("Which take do you like?", True, "Which take do you like?", []),
    ("Two looks would work.\n\n**Warm or cold?**", True, "Two looks would work.\n\n**Warm or cold?**", []),
    ("[question]\nWhich voice?\n1. Deep\n2. Bright", True, "Which voice?", ["Deep", "Bright"]),
    ("Made three stills.\n[QUESTION] Animate which one?\n- s1\n- s2\n- s3", True, "Made three stills.\n\nAnimate which one?", ["s1", "s2", "s3"]),
    ("", False, "", []),
])
def test_question_detection(text, asked, shown, options):
    assert agents.question_of(text) == (asked, shown, options)


def test_one_turn_at_a_time_and_stop(fake, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "30")
    thread = start("Take your time", "claude")["thread"]
    queued = say(thread["id"], "and another")  # waits behind the running turn (test_a_second_message_waits_its_turn)
    assert [q["text"] for q in queued["thread"]["queued"]] == ["and another"]
    for _ in range(2):
        say(thread["id"], "more")
    assert post(f"/api/agent/threads/{thread['id']}/messages", {"text": "too many"}).status_code == 409
    assert client.delete(f"/api/agent/threads/{thread['id']}").status_code == 409
    ends = time.time() + 10
    while not client.get(f"/api/agent/threads/{thread['id']}").json()["live"]["steps"] and time.time() < ends:
        time.sleep(0.1)
    live = client.get(f"/api/agent/threads/{thread['id']}").json()["live"]
    assert live["status"] == "running" and live["backend"] == "claude" and any(s["text"].startswith("tools:") for s in live["steps"])
    started = time.time()
    post(f"/api/agent/threads/{thread['id']}/cancel")
    done = settled(thread["id"])
    assert time.time() - started < 10 and done["turns"][-1]["status"] == "cancelled"
    assert done["queued"] == [] and [t["text"] for t in done["turns"] if t["role"] == "user"] == ["Take your time"]  # Stop stops what waits
    assert post(f"/api/agent/threads/{thread['id']}/messages", {"text": ""}).status_code == 400


def test_a_follow_up_keeps_the_conversations_own_tools(fake):
    # as on a DGX Spark: an enabled tool that cannot launch here (over ssh, no host set), which a follow-up must not pick up
    post("/api/agent/tools", {"tools": [*agents.default_tools(), {"id": "fake", "label": "Fake", "command": [sys.executable, "-c", "pass"],
                                                                  "via": "ssh", "enabled": True}]})
    thread = start("Just talk", "claude")["thread"]  # started with no tools
    settled(thread["id"])
    say(thread["id"], "And now?")  # the page sends no tools with a reply
    done = settled(thread["id"])
    assert done["tools"] == [] and done["turns"][-1]["status"] == "done", done["turns"][-1]
    second = calls_of(fake, "claude")[-1]
    assert "--resume" in second["args"]  # the same session, so the turn did not fail on a tool it was never given
    assert json.loads((agents.HOME / "agent" / thread["id"] / "mcp.json").read_text())["mcpServers"] == {}
    # a thread with a tool keeps that one, and a tool turned off since is left out rather than failing the turn
    post("/api/agent/tools", {"tools": [*agents.default_tools(), {"id": "fake", "label": "Fake", "command": [sys.executable, "-c", "pass"],
                                                                  "enabled": True}]})
    with_tool = start("Use it", "claude", tools=["fake"])["thread"]
    settled(with_tool["id"])
    tools = agents.tools()
    for tool in tools:
        tool["enabled"] = tool["id"] != "fake"
    agents.save_tools({"tools": tools})
    say(with_tool["id"], "Again")
    assert settled(with_tool["id"])["tools"] == []


def test_a_thread_stays_with_its_own_agent(fake):
    thread = start("Build a set", "claude")["thread"]
    settled(thread["id"])
    refused = post(f"/api/agent/threads/{thread['id']}/messages", {"text": "Now you, Codex", "backend": "codex"})
    assert refused.status_code == 409 and "Claude Code" in refused.json()["error"]
    say(thread["id"], "Carry on", backend="claude")
    done = settled(thread["id"])
    assert done["backend"] == "claude" and not calls_of(fake, "codex") and calls_of(fake, "claude")[-1]["stdin"] == "Carry on"


def test_a_second_message_waits_its_turn(fake, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1.5")
    thread = start("Build a set", "claude")["thread"]
    said = say(thread["id"], "Then light it")
    assert said["task"]["status"] == "queued" and said["thread"]["queued"][0]["text"] == "Then light it"
    assert [t["text"] for t in said["thread"]["turns"] if t["role"] == "user"] == ["Build a set"]  # it joins when its turn comes
    ends = time.time() + 30
    while time.time() < ends:
        done = client.get(f"/api/agent/threads/{thread['id']}").json()
        if not done["live"] and not done["queued"]:
            break
        time.sleep(0.1)
    said = [(t["role"], t["text"]) for t in done["turns"] if t["role"] in ("user", "agent")]
    assert said == [("user", "Build a set"), ("agent", "Made render.png"), ("user", "Then light it"), ("agent", "Made render.png")]
    second = calls_of(fake, "claude")[-1]
    assert "--resume" in second["args"] and second["stdin"] == "Then light it"  # the first answer was kept, and its session


def test_a_failed_turn_says_the_clis_words_once(fake, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "expired")
    thread = start("Build a set", "claude")["thread"]
    last = settled(thread["id"])["turns"][-1]
    assert last["status"] == "failed" and "claude auth login" in last["error"] and "Not logged in" in last["error"]
    assert last["text"] == ""  # not the same words again above the error


def test_threads_are_for_this_computer_only(fake):
    assert client.get("/api/agent/threads", headers={"host": "studio.example.com"}).status_code == 403
    assert post("/api/agent/threads", {"text": "x", "backend": "claude"}, headers={"origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/agent/threads", content="text=x", headers={"content-type": "text/plain"}).status_code in (415, 422)
    assert client.delete("/api/agent/threads/th-0000-000000-000000", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert client.get("/api/agent/threads/../settings").status_code == 404


def test_the_studio_mcp_makes_voice_songs_timelines_and_exports(tmp_path):
    import http.server
    import subprocess
    import sys
    import threading
    seen, project = [], {"id": "p1", "name": "Cut", "timeline": []}

    class Studio(http.server.BaseHTTPRequestHandler):
        def answer(self, value, code=200):
            data = json.dumps(value).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def body(self):
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"null")

        def do_GET(self):
            seen.append(("GET", self.path))
            if self.path == "/api/projects":
                return self.answer([{"id": "p1", "name": "Cut"}])
            if self.path == "/api/projects/p1":
                return self.answer(project)
            if self.path.startswith("/api/probe"):
                return self.answer({"duration": 8.1})
            self.answer({"error": "no"}, 404)

        def do_POST(self):
            body = self.body()
            seen.append(("POST", self.path, body))
            self.answer({"id": "j9", "kind": {"/api/voice/takes": "voice", "/api/music/takes": "music"}.get(self.path, (body or {}).get("kind")),
                         "status": "queued", "title": "t", "outputs": [], "log": "x"})

        def do_PUT(self):
            body = self.body()
            seen.append(("PUT", self.path, body))
            project.update(body)
            self.answer(project)

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Studio)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    root = Path(__file__).resolve().parent.parent
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]
    for i, (name, arguments) in enumerate([("queue_voice", {"text": "[quiet] We should go.", "ref_audio": "x.wav"}),
                                           ("queue_song", {"caption": "slow synth", "seconds": 30}),
                                           ("add_to_timeline", {"path": "renders/a.mp4"}),
                                           ("export_timeline", {"size": "1344x768"})], start=2):
        lines.append({"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
    try:
        done = subprocess.run([sys.executable, str(root / "mcp/studio-mcp.py")], input="".join(json.dumps(l) + "\n" for l in lines),
                              capture_output=True, text=True, timeout=30, env={"STUDIO_URL": f"http://127.0.0.1:{httpd.server_port}", "PATH": "/usr/bin:/bin"})
    finally:
        httpd.shutdown()
    replies = {r["id"]: r["result"] for r in map(json.loads, done.stdout.splitlines())}
    names = [t["name"] for t in replies[1]["tools"]]
    for name in ("queue_voice", "queue_song", "list_projects", "add_to_timeline", "export_timeline"):
        assert name in names and name in agents.STUDIO_TOOLS
    assert all(not replies[i].get("isError") for i in (2, 3, 4, 5)), replies
    posts = {s[1]: s[2] for s in seen if s[0] == "POST"}
    assert posts["/api/voice/takes"] == {"text": "[quiet] We should go."}  # no reference clip: cloning needs the consent box
    assert posts["/api/music/takes"] == {"caption": "slow synth", "seconds": 30}
    assert project["timeline"] == [{"path": "renders/a.mp4", "in": 0.0, "out": 0.0, "duration": 8.1}]
    assert posts["/api/jobs"]["kind"] == "export" and posts["/api/jobs"]["params"]["width"] == 1344 and posts["/api/jobs"]["params"]["clips"] == project["timeline"]
