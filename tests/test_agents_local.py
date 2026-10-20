"""The local harness (app/agents.py drive): a local model whose server takes tool calls runs in the Agent room with the
Studio's MCP tools, through the Studio's own loop. python3 -m pytest tests/test_agents_local.py. No network: the model
server is a scripted stand-in on 127.0.0.1 and the tool is tests/test_agents.py's stand-in MCP server."""
import http.server
import json
import threading
import time

import pytest

from test_agents import PY, agents, client, fake, post, slate  # noqa: F401  (fake is the fixture)


class Model:
    """An OpenAI-compatible server: /v1/models, llama.cpp's /props, and /v1/chat/completions answered by `reply`."""

    def __init__(self, reply, tools=True):  # tools None: no /props (not llama.cpp), so the Studio asks with a small call
        self.asked, self.reply, self.tools = [], reply, tools
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, body, code=200):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/v1/models":
                    return self.send({"data": [{"id": "nvidia-nemotron-3.5-bolt"}]})
                if self.path == "/props" and outer.tools is not None:
                    return self.send({"chat_template_caps": {"supports_tool_calls": outer.tools}})
                self.send({}, 404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.asked.append(body)
                message = outer.reply(body, len(outer.asked))
                self.send({"choices": [{"message": {"role": "assistant", **message},
                                        "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
                           "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"

    def close(self):
        self.httpd.shutdown()


def call(name: str, arguments, number: int = 1) -> dict:
    return {"id": f"c{number}", "type": "function",
            "function": {"name": name, "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments)}}


@pytest.fixture
def local(fake, monkeypatch):
    """A fake tool saved, the real slate.find_local, and a model server the test scripts."""

    monkeypatch.setattr(slate, "find_local", lambda given="": (slate.endpoint_of(given), slate.local_models(slate.endpoint_of(given)) or [])
                        if given and slate.local_models(slate.endpoint_of(given)) is not None else (None, []))
    agents.tool_checks.clear()
    post("/api/agent/tools", {"id": "fake", "label": "Fake", "command": [PY, str(fake["tmp"] / "mcp.py")], "env": {}})
    servers = []

    def serve(reply, tools=True):
        model = Model(reply, tools)
        servers.append(model)
        post("/api/brains/settings", {"local_endpoint": model.url})
        return model
    yield serve
    for model in servers:
        model.close()


def settled(thread_id: str, seconds: float = 20) -> dict:
    ends = time.time() + seconds
    while time.time() < ends:
        thread = client.get(f"/api/agent/threads/{thread_id}").json()
        if not thread["live"]:
            return thread
        time.sleep(0.1)
    raise AssertionError(thread)


def record(task_id: str) -> dict:
    return json.loads((agents.HOME / "agent" / task_id / "task.json").read_text())


def test_a_local_model_that_takes_tool_calls_drives_the_studio_and_is_the_automatic_agent(local):
    def reply(body, n):
        if body["messages"][-1]["role"] == "tool":
            return {"content": "Made the scene: render.png is in the Library."}
        return {"content": "", "tool_calls": [call("get_scene_info", {})]}
    model = local(reply)
    found = {b["id"]: b for b in client.get("/api/brains").json()["backends"]}
    assert found["local"]["harness"] is True and found["local"]["found"]
    settings = client.get("/api/brains").json()["settings"]
    assert settings["default"] == "local" and settings["agent"] == "local"  # the automatic brain, and so the automatic agent
    saved = json.loads((agents.HOME / ".studio-agents/settings.json").read_text())
    assert saved.get("agent") == ""  # never written down as a choice
    answer = post("/api/agent/threads", {"text": "Build a diner booth", "tools": ["fake"]}).json()  # no backend: the room's own
    assert answer["task"]["backend"] == "local" and answer["task"]["tools"] == ["fake"]
    thread = settled(answer["thread"]["id"])
    roles = [(t["role"], t["text"]) for t in thread["turns"]]
    assert roles[0] == ("user", "Build a diner booth") and roles[-1] == ("agent", "Made the scene: render.png is in the Library.")
    assert ("tool", "tools: fake") in roles and ("tool", "→ fake · get_scene_info") in roles
    task = record(answer["task"]["id"])
    assert task["status"] == "done" and task["calls"] == {"answers": 2, "made": 1, "bad": 0, "failed": 0}
    # the model was offered the server's tools as functions, with the Studio's instructions, and read the tool's answer
    first, second = model.asked
    assert [f["function"]["name"] for f in first["tools"]] == ["get_scene_info", "execute_blender_code"]
    assert first["model"] == "nvidia-nemotron-3.5-bolt" and "Never describe a call instead of making it" in first["messages"][0]["content"]
    assert second["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": "{}"}
    stream = [json.loads(line) for line in (agents.HOME / "agent" / task["id"] / "stream.jsonl").read_text().splitlines()]
    assert [e["type"] for e in stream] == ["start", "answer", "tool", "answer", "end"]
    assert "→ fake.get_scene_info" in (agents.HOME / "agent" / task["id"] / "log.txt").read_text()
    # a person's choice of agent wins over the automatic one
    assert post("/api/brains/settings", {"agent": "claude"}).json()["settings"]["agent"] == "claude"


def test_bad_calls_are_answered_and_paths_stay_in_the_folders(local):
    def reply(body, n):
        if n == 1:
            return {"content": "", "tool_calls": [call("nothing_here", {}, 1), call("execute_blender_code", "{not json", 2),
                                                  call("execute_blender_code", {"filepath": "/etc/passwd"}, 3),
                                                  call("execute_blender_code", {"output": "../../outside.png"}, 4),
                                                  call("execute_blender_code", {"image": "renders/a.png", "image_prompt": "/a/b c"}, 5)]}
        return {"content": "Done."}
    model = local(reply)
    answer = post("/api/agent/threads", {"text": "Go", "backend": "local", "tools": ["fake"]}).json()
    settled(answer["thread"]["id"])
    task = record(answer["task"]["id"])
    assert task["calls"] == {"answers": 2, "made": 5, "bad": 4, "failed": 0}
    said = [m["content"] for m in model.asked[1]["messages"] if m["role"] == "tool"]
    assert said[0].startswith("There is no tool called nothing_here") and "not a JSON object" in said[1]
    assert "outside this conversation's folder" in said[2] and "climbs out" in said[3] and said[4] == "{}"
    # a path inside the thread's own folder is fine
    work = agents.HOME / "agent" / answer["thread"]["id"]
    assert agents.confine({"path": str(work / "a.png")}, work) is None and agents.confine({"path": str(agents.HOME / "renders/a.png")}, work) is None
    assert agents.confine({"path": str(agents.HOME / ".studio-agents/settings.json")}, work)
    # an absolute path is checked under any key, not only one that reads like a path
    assert agents.confine({"video": "/etc/passwd"}, work) and agents.confine({"source": "~/.ssh/id_ed25519"}, work)
    assert agents.confine({"prompt": "/imagine a lighthouse"}, work) is None  # a prompt is words, never a path


def test_the_step_limit_ends_the_turn_with_a_summary(local, monkeypatch):
    monkeypatch.setattr(agents, "LOCAL_STEPS", 2)
    model = local(lambda body, n: {"content": "Summary: nothing finished."} if "tools" not in body
                  else {"content": "", "tool_calls": [call("get_scene_info", {})]})
    answer = post("/api/agent/threads", {"text": "Loop", "backend": "local", "tools": ["fake"]}).json()
    thread = settled(answer["thread"]["id"])
    task = record(answer["task"]["id"])
    assert task["status"] == "done" and task["limit"] == "steps" and len(model.asked) == 3 and "tools" not in model.asked[-1]
    assert thread["turns"][-1]["text"] == "Summary: nothing finished."
    assert any(t["text"] == "stopped at the step limit (2 answers)" for t in thread["turns"] if t["role"] == "tool")


def test_a_promise_without_a_call_is_nudged_once_then_kept(local):
    model = local(lambda body, n: {"content": "I'll queue the still now."} if n == 1 else {"content": "", "tool_calls": [call("get_scene_info", {})]}
                  if n == 2 else {"content": "Queued it."})
    answer = post("/api/agent/threads", {"text": "A still", "backend": "local", "tools": ["fake"]}).json()
    thread = settled(answer["thread"]["id"])
    assert thread["turns"][-1]["text"] == "Queued it." and len(model.asked) == 3
    assert "Make the calls now" in model.asked[1]["messages"][-1]["content"]
    # after a call, "Let me know ... I'll queue it" is the summary, not a promise
    assert not agents.PROMISE.search("Let me know when you are ready.")


def test_stop_ends_a_local_turn(local):
    gate = threading.Event()
    local(lambda body, n: gate.wait(10) and {"content": "late"} or {"content": "late"})
    answer = post("/api/agent/threads", {"text": "Wait", "backend": "local", "tools": ["fake"]}).json()
    time.sleep(0.5)
    post(f"/api/agent/threads/{answer['thread']['id']}/cancel")
    thread = settled(answer["thread"]["id"], 5)
    gate.set()
    assert record(answer["task"]["id"])["status"] == "cancelled" and thread["turns"][-1]["status"] == "cancelled"


def test_a_server_without_tool_calls_stays_a_conversation(local):
    model = local(lambda body, n: {"content": "Here is a prompt to paste."}, tools=False)
    found = {b["id"]: b for b in client.get("/api/brains").json()["backends"]}
    assert found["local"]["found"] and found["local"]["harness"] is False
    assert client.get("/api/brains").json()["settings"]["agent"] == ""
    answer = post("/api/agent/threads", {"text": "A still", "backend": "local", "tools": ["fake"]}).json()
    assert answer["task"]["tools"] == []
    thread = settled(answer["thread"]["id"])
    assert thread["turns"][-1]["text"] == "Here is a prompt to paste." and "tools" not in model.asked[-1]


def test_a_server_without_props_is_asked_aside_with_one_small_call(local):
    def reply(body, n):
        if body["messages"][-1]["content"] == "Call the ready tool.":
            time.sleep(0.5)  # a busy server: the page's look does not wait for it
            return {"content": "", "tool_calls": [call("ready", {})]}
        return {"content": "Hello."}
    model = local(reply, tools=None)
    started = time.time()
    first = {b["id"]: b for b in client.get("/api/brains").json()["backends"]}["local"]
    assert first["found"] and first["harness"] is False and time.time() - started < 0.5
    ends = time.time() + 10
    while time.time() < ends and not {b["id"]: b for b in client.get("/api/brains").json()["backends"]}["local"]["harness"]:
        time.sleep(0.1)
    assert {b["id"]: b for b in client.get("/api/brains").json()["backends"]}["local"]["harness"] is True
    assert sum(1 for body in model.asked if body["messages"][-1]["content"] == "Call the ready tool.") == 1  # asked once, then kept
