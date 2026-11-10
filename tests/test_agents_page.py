"""The Agent room's page (app/static/agent.js) in node, with the shell's stand-ins (tests/test_shell_page.py's harness):
python3 -m pytest tests/test_agents_page.py. Skipped where node is not installed."""
from test_shell_page import run

BRAINS = """BR.data = { default: "claude", settings: {}, backends: [
    { id: "claude", label: "Claude Code", found: true, where: "here", version: "2.1.4 (Claude Code)", harness: true },
    { id: "codex", label: "Codex · ChatGPT sign-in", found: false, where: "ssh:user@workstation", harness: true },
    { id: "gemini", label: "Gemini CLI", found: false, where: "here", signin: "Run `gemini` once and sign in." },
    { id: "grok", label: "Grok CLI", found: false, where: "here", error: "ssh to x failed" },
    { id: "hermes", label: "Hermes", found: true, where: "here", version: "Hermes Agent v0.21.5", session: true },
    { id: "local", label: "Local model server", kind: "http", found: true, models: ["qwen3"] }] };
  BR.tools = { tools: [{ id: "blender", label: "Blender" }, { id: "blockout", label: "Blockout" }, { id: "studio", label: "This Studio" }] };
  AG.tasks = []; AG.threads = []; answers["/api/agent/tasks"] = { tasks: [] }; answers["/api/agent/threads"] = { threads: [] };
  const cls = (root, c) => find(root, n => (" " + ((n.attrs && n.attrs.class) || "") + " ").includes(" " + c + " "));
  const all = (root, test, out = []) => { if (!root || typeof root !== "object") return out; if (Array.isArray(root)) { root.forEach(n => all(n, test, out)); return out; } if (test(root)) out.push(root); all(root.kids, test, out); return out; };
  // the page's el() flattens its children one level: an array left inside would be shown as "[object HTMLElement]"
  const nested = root => !root || typeof root !== "object" ? 0 : Array.isArray(root) ? root.reduce((n, k) => n + nested(k), 0) : (root.kids || []).filter(Array.isArray).length + nested(root.kids);
  const card = (page, id) => all(page, n => n.tag === "button" && /agr-card/.test(n.attrs.class || ""))[["claude", "codex", "hermes", "gemini", "grok", "local"].indexOf(id)];
"""
THREAD = """const thread = { id: "th-1018-100000-aaaaaa", title: "A diner teaser", backend: "claude", session: true, status: "idle", waiting: true, live: null, turns: [
    { id: "u1", role: "user", text: "A diner teaser", attachments: [], at: 1 },
    { id: "t1", role: "tool", text: "→ studio · queue_still", at: 2 }, { id: "t2", role: "tool", text: "scout done → s1.png", at: 3 },
    { id: "a1", role: "agent", text: "Made a still.", attachments: ["agent/th-1018-100000-aaaaaa/diner.png"], jobs: ["j1"], status: "done", question: false, at: 4, backend: "claude" },
    { id: "u2", role: "user", text: "Now a clip", attachments: [], at: 5 },
    { id: "a2", role: "agent", text: "Two ways to go.\\n\\nHow long should the clip be?", options: ["5 s", "10 s"], question: true, status: "done", attachments: [], jobs: [], at: 6, backend: "claude" }] };
  jobs.push({ id: "j1", status: "done", outputs: ["renders/clip1.mp4"] });
"""


def test_the_chooser_shows_found_remote_and_missing_agents():
    out = run(BRAINS + """
      const at = TABS.indexOf("Settings"); if (at >= 0) TABS.splice(at, 1);  // first without a Settings page
      let page = viewAgent(); out.backend = AG.backend;
      out.states = ["claude", "codex", "hermes", "gemini", "grok", "local"].map(id => [text(card(page, id)), card(page, id).attrs["aria-disabled"] === "true"]);
      out.under = text(cls(page, "agr-under"));
      out.setup = text(find(page, n => n.tag === "a" && n.attrs.class === "ag-setup"));
      card(page, "hermes").attrs.onclick(); page = viewAgent(); out.hermes = [AG.backend, text(cls(page, "agr-under"))];
      out.hermesOn = card(page, "hermes").attrs["aria-checked"];
      if (typeof viewSettings !== "function") globalThis.viewSettings = () => el("div"); TABS.push("Settings"); page = viewAgent();
      const link = find(page, n => n.tag === "a" && n.attrs.class === "ag-setup"); out.settings = text(link); link.attrs.onclick({}); out.tab = tab;""")
    assert out["backend"] == "claude"
    states = dict(zip(["claude", "codex", "hermes", "gemini", "grok", "local"], out["states"]))
    assert "Ready · 2.1.4" in states["claude"][0] and "Uses the Studio" in states["claude"][0] and not states["claude"][1]
    assert "On workstation" in states["codex"][0] and not states["codex"][1]  # over SSH: there, so not greyed
    assert "Conversation" in states["hermes"][0] and "Ready" in states["hermes"][0] and not states["hermes"][1]
    assert "Not found" in states["gemini"][0] and states["gemini"][1] and "Click to connect" in states["gemini"][0] and "sign in" in states["gemini"][0]  # not ready: its card connects it, the reason in its title
    assert "Not reached" in states["grok"][0] and states["grok"][1]
    assert not states["local"][1]
    assert "It may use" in out["under"] and "This Studio" in out["under"]
    assert out["setup"] == "Set up on Machine → Brains & tools"  # no Settings page yet: Brains & tools
    assert out["hermes"][0] == "hermes" and "does not drive the Studio's tools" in out["hermes"][1] and out["hermesOn"] == "true"
    assert out["settings"] == "Set up in Settings → Connections" and out["tab"] == "Settings"


def test_a_new_conversation_starts_and_opens_its_thread():
    out = run(BRAINS + """
      answers["/api/agent/tasks"] = { id: "0101-000000-abcdef", status: "queued", thread: "th-1018-100000-bbbbbb" };
      answers["/api/agent/threads/th-1018-100000-bbbbbb"] = { id: "th-1018-100000-bbbbbb", title: "A teaser", backend: "claude", turns: [{ id: "u1", role: "user", text: "A teaser", attachments: [] }],
        live: { task: "0101-000000-abcdef", status: "running", backend: "claude", steps: [{ text: "tools: studio" }, { text: "→ studio · queue_clip" }], outputs: [], jobs: [] } };
      let page = viewAgent(); out.start = text(page);
      button(page, "A teaser").attrs.onclick(); page = viewAgent();
      await button(page, "Send").attrs.onclick();
      out.sent = calls.find(c => c[0] === "/api/agent/tasks" && c[1]); out.cur = AG.cur; out.prompt = AG.prompt;
      answers["/api/agent/threads"] = { threads: [{ id: "th-1018-100000-bbbbbb", title: "A teaser", backend: "claude", status: "running", turns: 1, updated: Date.now() / 1000 }] };
      await agThreadsLoad(); page = viewAgent(); out.live = text(cls(page, "agr-livebox")); out.list = text(cls(page, "agr-list"));
      out.nextOn = !button(page, "Send").attrs.disabled;  // a message written while it works waits its turn (agents.submit)
      await button(cls(page, "agr-livebox"), "Stop").attrs.onclick(); out.stop = calls.some(c => c[0] === "/api/agent/threads/th-1018-100000-bbbbbb/cancel");
      clearInterval(AG.timer); AG.timer = null;""")
    assert "Describe the shot" in out["start"] and "Choose your agent" in out["start"]
    body = out["sent"][1]
    assert body["backend"] == "claude" and "studio" in body["tools"] and body["prompt"].startswith("Make a 10-second teaser")
    assert out["cur"] == "th-1018-100000-bbbbbb" and out["prompt"] == ""
    assert "is working" in out["live"] and "→ studio · queue_clip" in out["live"]
    assert "A teaser" in out["list"] and "working" in out["list"]
    assert out["nextOn"] and out["stop"]


def test_a_question_card_takes_the_answer_and_the_thread_shows_what_it_made():
    out = run(BRAINS + THREAD + """
      answers["/api/agent/threads"] = { threads: [{ id: thread.id, title: thread.title, backend: "claude", status: "idle", waiting: true, turns: 4, last: "How long?", updated: 1 },
        { id: "th-1018-090000-cccccc", title: "Older", backend: "hermes", status: "idle", turns: 2, updated: 0 }] };
      answers["/api/agent/threads/" + thread.id] = thread; await agThreadsLoad();
      let page = viewAgent(); out.list = text(cls(page, "agr-list"));
      await find(page, n => n.attrs && n.attrs["data-thread"] === thread.id).attrs.onclick(); page = viewAgent();
      const q = cls(page, "agr-q"); out.q = text(q); out.composers = all(page, n => n.tag === "textarea").map(n => n.attrs["data-ag"]);
      out.nested = nested(page); out.steps = text(cls(page, "agr-steps")); out.user = all(page, n => /agr-user/.test(n.attrs && n.attrs.class || "")).map(n => text(n).trim());
      const deliv = cls(page, "agr-deliv"); out.delivered = text(deliv); out.timeline = !!button(deliv, "+ timeline");
      await button(q, "10 s").attrs.onclick(); out.option = calls.filter(c => c[0] === "/api/agent/threads/" + thread.id + "/messages").map(c => c[1]);
      AG.reply = "Make it 8 s instead"; page = viewAgent(); await button(cls(page, "agr-q"), "Send answer").attrs.onclick();
      out.answer = calls.filter(c => c[0] === "/api/agent/threads/" + thread.id + "/messages").map(c => c[1].text);
      clearInterval(AG.timer); AG.timer = null;""")
    assert "A diner teaser" in out["list"] and "Needs you" in out["list"] and "Older" in out["list"]
    assert "Claude Code asks" in out["q"] and "How long should the clip be?" in out["q"] and "5 s" in out["q"]
    assert out["nested"] == 0
    assert out["composers"] == ["reply"]  # the answer box is the only one while the agent waits
    assert "→ studio · queue_still" in out["steps"] and "scout done" in out["steps"]
    assert out["user"] == ["A diner teaser", "Now a clip"]
    assert "Delivered · 2" in out["delivered"] and "diner.png" in out["delivered"] and "clip1.mp4" in out["delivered"] and out["timeline"]
    assert out["option"][0]["text"] == "10 s" and out["option"][0]["backend"] == "claude"
    assert out["answer"] == ["10 s", "Make it 8 s instead"]


def test_an_agents_words_are_never_html():
    out = run(BRAINS + """const nodes = agText("**Done.** Saved `a.png`\\n\\n- one\\n- two\\n\\n<img src=x onerror=alert(1)>");
      out.tags = JSON.stringify(nodes).match(/"tag":"[a-z]+"/g); out.text = text(nodes);
      out.html = JSON.stringify(nodes).includes('"html"'); out.nested = nested(nodes);""")
    assert '"tag":"b"' in out["tags"] and '"tag":"code"' in out["tags"] and '"tag":"li"' in out["tags"] and '"tag":"img"' not in out["tags"]
    assert "<img src=x onerror=alert(1)>" in out["text"] and not out["html"] and out["nested"] == 0


def test_a_remote_not_reached_or_a_cli_not_signed_in_is_not_ready():
    out = run(BRAINS + """
      BR.data.backends[1] = { id: "codex", label: "Codex", found: false, where: "ssh:user@workstation", harness: true,
        error: "ssh to user@workstation failed: Host key verification failed.. The Studio signs in ... run `ssh user@workstation true` once" };
      BR.status.claude = { state: "signed_out", detail: "Not signed in" };
      let page = viewAgent();
      out.claude = [text(card(page, "claude")), card(page, "claude").attrs["aria-disabled"] === "true"];
      out.codex = [text(card(page, "codex")), card(page, "codex").attrs["aria-disabled"] === "true"];
      out.backend = AG.backend; out.hint = text(cls(page, "ag-hint"));
      BR.status.claude = { state: "signed_in", detail: "Claude subscription" }; page = viewAgent();
      out.after = [text(card(page, "claude")), card(page, "claude").attrs["aria-disabled"] === "true"];""")
    assert "Not signed in" in out["claude"][0] and out["claude"][1]
    assert "Not reached" in out["codex"][0] and out["codex"][1] and "ssh user@workstation true" in out["codex"][0]  # its title says what to do
    assert out["backend"] not in ("claude", "codex") and "Claude Code" in out["hint"] and "Codex" in out["hint"]
    assert "Ready" in out["after"][0] and not out["after"][1]


def test_a_reply_goes_to_the_threads_own_agent():
    out = run(BRAINS + THREAD + """
      thread.backend = "hermes"; thread.tools = []; thread.turns.forEach(t => { if (t.backend) t.backend = "hermes"; });
      answers["/api/agent/threads"] = { threads: [{ id: thread.id, title: thread.title, backend: "hermes", status: "idle", turns: 4, updated: 1 }] };
      answers["/api/agent/threads/" + thread.id] = thread; await agThreadsLoad();
      AG.backend = "claude"; let page = viewAgent();
      await find(page, n => n.attrs && n.attrs["data-thread"] === thread.id).attrs.onclick();
      AG.backend = "claude";  // the card chosen above is another agent
      page = viewAgent(); out.bar = text(cls(page, "agr-bar"));
      AG.reply = "Shorter, please"; page = viewAgent(); await button(cls(page, "agr-q"), "Send answer").attrs.onclick();
      out.sent = calls.filter(c => c[0] === "/api/agent/threads/" + thread.id + "/messages").map(c => c[1]);
      // a message written while a turn runs is shown as queued
      thread.live = { task: "t2", status: "running", backend: "hermes", steps: [], outputs: [], jobs: [] }; thread.queued = [{ task: "t3", text: "And a title card", attachments: [] }];
      page = viewAgent(); out.queued = text(cls(page, "agr-queued"));
      clearInterval(AG.timer); AG.timer = null;""")
    assert "Hermes" in out["bar"] and "Claude" not in out["bar"]
    assert out["sent"] == [{"text": "Shorter, please", "backend": "hermes", "attach": []}]  # no tools: the thread keeps its own
    assert "Queued" in out["queued"] and "And a title card" in out["queued"]


def test_a_signed_out_agent_is_never_called_not_on_this_machine():
    out = run(BRAINS + """
      BR.status = { claude: { state: "signed_out" } };
      out.said = agWhyNot(BR.data.backends.filter(b => ["claude", "codex", "gemini", "grok"].includes(b.id)));""")
    if out is None:
        return  # no node on this machine: the page's code is not run here, as for the other page tests
    assert "Claude Code is not signed in." in out["said"] and "Claude Code is not on this machine" not in out["said"]
    assert "Gemini CLI is not on this machine." in out["said"] and "Grok CLI is not reached." in out["said"]


def test_a_local_model_that_takes_tool_calls_uses_the_studio_and_is_the_rooms_first_choice():
    out = run(BRAINS + """
      BR.data.backends[5] = { id: "local", label: "NVIDIA Nemotron 3.5 Bolt (local)", named: true, kind: "http", found: true, harness: true, models: ["nvidia-nemotron-3.5-bolt"] };
      BR.data.default = "local"; BR.data.automatic = true; BR.data.settings = { agent: "local" };
      let page = viewAgent(); out.backend = AG.backend; out.card = text(card(page, "local")); out.under = text(cls(page, "agr-under"));
      button(page, "A teaser").attrs.onclick(); page = viewAgent(); await button(page, "Send").attrs.onclick();
      out.sent = calls.find(c => c[0] === "/api/agent/tasks" && c[1]);
      // a local server without tool calls stays a conversation, and says why
      BR.data.backends[5].harness = false; BR.data.settings = {}; AG.backend = "local"; page = viewAgent();
      out.plain = [text(card(page, "local")), text(cls(page, "agr-under"))];""")
    assert out["backend"] == "local" and "Uses the Studio" in out["card"] and "Nemotron 3.5 Bolt" in out["card"]
    assert "It may use" in out["under"] and "This Studio" in out["under"]
    assert out["sent"][1]["backend"] == "local" and "studio" in out["sent"][1]["tools"]
    assert "Conversation" in out["plain"][0] and "does not take tool calls" in out["plain"][1]


def test_a_new_conversations_model_is_picked_beside_send_from_the_ready_ones():
    out = run(BRAINS + """
      let page = viewAgent();
      const pick = find(page, n => n.tag === "select" && n.attrs && n.attrs["data-ag-model"]);
      out.options = all(pick, n => n.tag === "option").map(n => n.attrs.value); out.value = pick.value;
      pick.attrs.onchange({ target: { value: "hermes" } }); out.chosen = AG.backend;""")
    assert out["options"] == ["claude", "codex", "hermes", "local"]  # the ready ones, as their cards say: codex over SSH counts
    assert out["value"] == "claude" and out["chosen"] == "hermes"


def test_a_conversation_keeps_its_model_and_its_steps_scroll_in_their_box():
    out = run(BRAINS + THREAD + """
      thread.live = { status: "running", backend: "claude", steps: Array.from({ length: 12 }, (_, i) => ({ text: "→ step " + (i + 1) })), outputs: [], jobs: [] };
      AG.cur = thread.id; AG.thread = thread; let page = viewAgent();
      out.pick = !!find(page, n => n.tag === "select" && n.attrs && n.attrs["data-ag-model"]);
      const box = find(page, n => n.attrs && n.attrs["data-keep-scroll"] === "agent-steps");
      out.steps = all(box, n => n.tag === "div" && !n.attrs["data-keep-scroll"]).map(text);
      out.scrolls = /overflow-y:auto/.test(box.attrs.style);""")
    assert out["pick"] is False
    assert out["steps"][0] == "→ step 1" and out["steps"][-1] == "→ step 12" and len(out["steps"]) == 12  # every step, not the last six
    assert out["scrolls"] is True


def test_a_new_turns_steps_follow_the_newest_again_after_the_last_one_was_scrolled_up():
    out = run(BRAINS + THREAD + """
      const box = { scrollTop: 0, scrollHeight: 500 };
      document.querySelector = sel => sel.includes("agent-steps") ? box : null; globalThis.requestAnimationFrame = f => f();
      const live = task => ({ task, status: "running", backend: "claude", steps: [{ text: "→ a step" }], outputs: [], jobs: [] });
      thread.live = live("t1"); AG.cur = thread.id; AG.thread = thread; viewAgent(); out.first = box.scrollTop;
      AG.stepsEnd = false; box.scrollTop = 40; viewAgent(); out.scrolledUp = box.scrollTop;
      thread.live = live("t2"); viewAgent(); out.next = box.scrollTop;""")
    assert out["first"] == 500  # a live turn follows its newest step
    assert out["scrolledUp"] == 40  # scrolled up, it stays where it was
    assert out["next"] == 500  # the next turn follows again


def test_the_model_picker_leaves_out_a_ready_backend_the_room_does_not_offer():
    # the server may find an agent the room does not offer (GitHub Copilot CLI, out of the room since 4.4.1)
    out = run(BRAINS + """
      BR.data.backends.push({ id: "copilot", label: "GitHub Copilot CLI", found: true, where: "here", harness: true },
        { id: "mine", label: "My agent", found: true, where: "here", local_agent: true });
      let page = viewAgent();
      const pick = find(page, n => n.tag === "select" && n.attrs && n.attrs["data-ag-model"]);
      out.options = all(pick, n => n.tag === "option").map(n => n.attrs.value);""")
    assert "copilot" not in out["options"]
    assert out["options"] == ["claude", "codex", "hermes", "local", "mine"]  # a local agent is still offered


def test_a_default_the_room_no_longer_offers_is_neither_chosen_nor_listed():
    out = run(BRAINS + """
      BR.data.backends.push({ id: "copilot", label: "GitHub Copilot CLI", found: true, where: "here", harness: true });
      BR.data.settings = { agent: "copilot" }; BR.data.default = "copilot"; AG.backend = "copilot";
      let page = viewAgent();
      const pick = find(page, n => n.tag === "select" && n.attrs && n.attrs["data-ag-model"]);
      out.options = all(pick, n => n.tag === "option").map(n => n.attrs.value); out.backend = AG.backend; out.value = pick.value;""")
    assert "copilot" not in out["options"] and out["backend"] == "claude" and out["value"] == "claude"  # shown is sent


def test_with_only_an_agent_the_room_does_not_offer_ready_none_of_it_is_chosen():
    out = run(BRAINS + """
      BR.data.backends = [{ id: "copilot", label: "GitHub Copilot CLI", found: true, where: "here", harness: true }];
      AG.backend = "copilot"; viewAgent(); out.backend = AG.backend;""")
    assert out["backend"] == "claude"  # not ready here, so the room says so instead of sending to Copilot
