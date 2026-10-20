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


# a stand-in for the Studio's own MCP server: queue_still queues a job, wait_for_jobs and job_status find it done
STUDIO_MCP = r'''
import json, sys
made = []
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    if message["method"] == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "studio", "version": "1"}}
    elif message["method"] == "tools/list":
        result = {"tools": [{"name": "studio_state", "inputSchema": {"type": "object", "properties": {}}},
                            {"name": "queue_still", "inputSchema": {"type": "object", "required": ["prompt"], "properties": {
                                "prompt": {"type": "string"}, "count": {"type": "integer", "description": "1 to 12, default 4"},
                                "image_model": {"type": "string"}}}},
                            {"name": "wait_for_jobs", "inputSchema": {"type": "object", "required": ["ids"], "properties": {"ids": {"type": "array"}}}},
                            {"name": "job_status", "inputSchema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}}]}
    else:
        name, args = message["params"]["name"], message["params"]["arguments"]
        if name == "studio_state":
            out = {"presets": {"small": {"label": "Small", "size": [864, 480]}}, "qualities": {"fh4": "FastH3 4"}, "turbo": True,
                   "image_models": [{"id": "qwen", "label": "Qwen", "note": "fast"}, {"id": "flux3k-full", "label": "F", "note": "slow"}],
                   "platform": "mac", "version": "3"}
        elif name == "queue_still" and args.get("prompt") == "too long":
            out = None
        elif name == "queue_still":
            made.append(f"j{len(made) + 1}")
            out = {"id": made[-1], "kind": "scout", "status": "queued", "title": "agent: still", "outputs": [], "error": None}
        else:
            ids = args.get("ids") or [args.get("id")]
            out = [{"id": i, "kind": "scout", "status": "done", "outputs": [f"scouts/{i}/scout_s1.png"]} for i in ids]
            out = out if name == "wait_for_jobs" else out[0]
        result = {"content": [{"type": "text", "text": json.dumps(out, indent=1)}]} if out is not None else \
            {"content": [{"type": "text", "text": "the Studio said: a prompt is at most 2000 characters (400)"}], "isError": True}
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
'''


@pytest.fixture
def studio_tool(fake, local):
    path = fake["tmp"] / "studio-mcp.py"
    path.write_text(STUDIO_MCP)
    post("/api/agent/tools", {"id": "studio", "label": "Studio", "command": [PY, str(path)], "env": {}})
    return local


def test_the_method_plans_checks_what_was_made_and_reports_the_records(studio_tool, monkeypatch):
    monkeypatch.delenv("STUDIO_LOCAL_AGENT", raising=False)
    def reply(body, n):
        if body.get("response_format"):  # the checklist, in its JSON schema
            return {"content": json.dumps({"do": [{"tool": "queue_still", "calls": 2, "what": "a samurai; a ninja, one picture each",
                                                   "quote": "One picture of a samurai and one of a ninja"},
                                                  {"tool": "studio_state", "calls": 1, "what": "look", "quote": "One picture"},
                                                  {"tool": "export_timeline", "calls": 1, "what": "export", "quote": "and export it"}],
                                           "not_asked": ["a clip"]})}
        last = body["messages"][-1]
        if last["role"] == "tool" and '"status":"queued"' in last["content"]:
            ids = [json.loads(m["content"])["id"] for m in body["messages"] if m["role"] == "tool" and '"status":"queued"' in m["content"]]
            return {"content": "", "tool_calls": [call("wait_for_jobs", {"ids": ids}, n)]}
        if last["role"] == "user" and "Checked against your checklist" in last["content"]:
            return {"content": "", "tool_calls": [call("queue_still", {"prompt": "a ninja", "count": 1}, n)]}
        if last["role"] == "user":
            return {"content": "", "tool_calls": [call("queue_still", {"prompt": "a samurai", "count": 1}, n)]}
        return {"content": "Made the samurai and the ninja: scouts/j1/scout_s1.png and scouts/j2/scout_s1.png."}
    model = studio_tool(reply)
    answer = post("/api/agent/threads", {"text": "One picture of a samurai and one of a ninja", "backend": "local", "tools": ["studio"]}).json()
    thread = settled(answer["thread"]["id"])
    first, acting = model.asked[0], model.asked[1]
    # the checklist first, without tools; a look-up tool on it is left out
    assert first["response_format"]["type"] == "json_schema" and "tools" not in first and first["temperature"] == 0
    assert "queue_still x2: a samurai; a ninja, one picture each" in acting["messages"][-1]["content"] and "studio_state" not in acting["messages"][-1]["content"]
    assert "not asked: a clip" in acting["messages"][-1]["content"]
    assert "export_timeline" not in acting["messages"][-1]["content"]  # its quote is not words of the request: left off
    # the rules, the hints in the schema, and Bolt's reasoning left off
    assert "Do only what the filmmaker asked" in acting["messages"][0]["content"]
    schema = next(f for f in acting["tools"] if f["function"]["name"] == "queue_still")["function"]["parameters"]
    assert schema["properties"]["count"]["description"].startswith("how many pictures: exactly the number asked for")
    assert "unless the filmmaker names a model" in schema["properties"]["image_model"]["description"]
    assert acting["chat_template_kwargs"] == {"enable_thinking": False} and acting["temperature"] == 0.3
    # it stopped after one of the two: the check sent it back for the other, then it waited and finished
    checked = next(b for b in model.asked if b["messages"][-1]["role"] == "user" and "Checked against" in b["messages"][-1]["content"])
    assert "queue_still: 1 of the 2 call(s) on the checklist made" in checked["messages"][-1]["content"]
    assert "jobs still queued" not in checked["messages"][-1]["content"]  # it had waited for the samurai
    # tool answers are compact JSON
    assert any(m["role"] == "tool" and m["content"].startswith('{"id":"j1","kind":"scout","status":"queued"') for m in checked["messages"])
    # the answer ends with the records
    said = thread["turns"][-1]["text"]
    assert said.startswith("Made the samurai and the ninja") and "What the Studio's records say was made:" in said
    assert "- scout j1 (agent: still): done → scouts/j1/scout_s1.png" in said and "- scout j2 (agent: still): done → scouts/j2/scout_s1.png" in said
    task = record(answer["task"]["id"])
    stream = [json.loads(line) for line in (agents.HOME / "agent" / task["id"] / "stream.jsonl").read_text().splitlines()]
    assert [e["type"] for e in stream].count("verify") == 1 and stream[1]["type"] == "plan"
    saved = json.loads((agents.HOME / "agent" / task["id"] / "messages.json").read_text())
    assert saved["messages"][1]["content"] == "One picture of a samurai and one of a ninja" and saved["reply"] == said


def test_the_benchmark_can_turn_the_method_off_or_on_part_by_part(studio_tool, monkeypatch):
    monkeypatch.setenv("STUDIO_LOCAL_AGENT", "baseline")
    model = studio_tool(lambda body, n: {"content": "", "tool_calls": [call("studio_state", {})]} if n == 1 else {"content": "Nothing to make."})
    answer = post("/api/agent/threads", {"text": "What can you make?", "backend": "local", "tools": ["studio"]}).json()
    thread = settled(answer["thread"]["id"])
    assert len(model.asked) == 2 and "response_format" not in model.asked[0] and thread["turns"][-1]["text"] == "Nothing to make."
    assert "Do only what" not in model.asked[0]["messages"][0]["content"]
    assert model.asked[1]["messages"][-1]["content"].startswith("{\n")  # the tool's own answer, as it gave it
    assert agents.local_method() == {"parts": set(), "temperature": 0.3}
    monkeypatch.setenv("STUDIO_LOCAL_AGENT", "rules,think,temp=0.6")
    assert agents.local_method() == {"parts": {"rules", "think"}, "temperature": 0.6}
    monkeypatch.delenv("STUDIO_LOCAL_AGENT")
    assert agents.local_method()["parts"] == set(agents.LOCAL_METHOD)
    # studio_state, as the model reads it: the ids, and the fastest defaults
    state = json.loads(agents.compact_result("studio_state", json.dumps({"presets": {"small": {"label": "S", "size": [864, 480]}},
                                                                          "image_models": [{"id": "qwen", "label": "Qwen 2.1 Turbo"}, {"id": "flux3k-full", "label": "FLUX3-K Full"}],
                                                                          "qualities": {"fh4": "FastH3: 4 passes, fastest"}})))
    assert state["presets"] == {"small": "864x480"} and state["image_models"] == {"qwen": "Qwen 2.1 Turbo", "flux3k-full": "FLUX3-K Full"} and "qwen" in state["defaults"]["image_model"]
    assert state["qualities"] == {"fh4": "FastH3: 4 passes, fastest"}  # with their labels: which one is the fastest is in them



def test_a_call_that_failed_is_not_sent_again_unchanged(studio_tool, monkeypatch):
    monkeypatch.setenv("STUDIO_LOCAL_AGENT", "baseline")
    model = studio_tool(lambda body, n: {"content": "", "tool_calls": [call("queue_still", {"prompt": "too long"}, n)]} if n <= 3
                        else {"content": "It could not be queued."})
    answer = post("/api/agent/threads", {"text": "Go", "backend": "local", "tools": ["studio"]}).json()
    settled(answer["thread"]["id"])
    task = record(answer["task"]["id"])
    assert task["calls"] == {"answers": 4, "made": 3, "bad": 2, "failed": 1}  # sent once; the two repeats were answered here
    said = [m["content"] for m in model.asked[-1]["messages"] if m["role"] == "tool"]
    assert said[0] == "the Studio said: a prompt is at most 2000 characters (400)"
    assert said[1].startswith("This exact queue_still call failed already (the Studio said: a prompt is at most") and said[2] == said[1]
