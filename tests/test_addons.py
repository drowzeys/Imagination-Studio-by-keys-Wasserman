"""Local add-ons: no models, sign-ins or external network."""
import json
import os
import time
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "studio"))
sys.path.insert(0, str(ROOT / "app"))
import addons
import agents
import server


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    for key in ("CLIS", "ADDON_AGENTS", "HINTS"):
        monkeypatch.setattr(agents, key, getattr(agents, key).copy())
    for key in ("BACKENDS", "HARNESSES", "AGENTS", "SESSIONS"):
        monkeypatch.setattr(agents, key, tuple(getattr(agents, key)))
    monkeypatch.delenv("STUDIO_ADDONS", raising=False)


def folder(home, code="", client=""):
    at = home / "addons" / "sample"
    at.mkdir(parents=True)
    if code:
        (at / "server.py").write_text(code)
    if client:
        (at / "client.js").write_text(client)
    return at


def client(home):
    app = FastAPI()
    addons.attach(app, home, lambda body: {"queued": body})
    return TestClient(app)


def test_disabled_does_not_execute_local_code(tmp_path):
    folder(tmp_path, 'raise RuntimeError("must not run")', 'throw Error("must not load")')
    assert client(tmp_path).get('/api/addons').json() == []


def test_missing_folder_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ADDONS", "1")
    assert client(tmp_path).get('/api/addons').json() == []


def test_router_queue_and_assets_are_local(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ADDONS", "1")
    at = folder(tmp_path, '''def register(addon):
    @addon.router.post("/render")
    def render(body: dict):
        return addon.queue(body)
''', '/* local script */')
    (at / 'secret.py').write_text('secret')
    (tmp_path / 'outside.js').write_text('outside')
    (at / 'escape.js').symlink_to(tmp_path / 'outside.js')
    c = client(tmp_path)
    assert c.get('/api/addons').json()[0]['script'] == '/api/addons/sample/assets/client.js'
    assert c.post('/api/addons/sample/render', json={'kind': 'image'}).json() == {'queued': {'kind': 'image'}}
    assert c.post('/render', json={}).status_code == 404
    assert c.get('/api/addons/sample/assets/client.js').text == '/* local script */'
    for path in ['secret.py', 'escape.js', '%2e%2e/outside.js', '.hidden.js']:
        assert c.get('/api/addons/sample/assets/' + path).status_code == 404


def test_registered_routes_use_the_app_access_and_write_guards(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ADDONS", "1")
    monkeypatch.setenv("STUDIO_ACCESS_KEY", "on")
    monkeypatch.setattr(server, 'ACCESS_KEY', 'sample-access-key-for-tests')
    folder(tmp_path, '''def register(addon):
    @addon.router.post("/echo")
    def echo(body: dict): return body
''', '/* local */')
    app = FastAPI(middleware=server.app.user_middleware)
    addons.attach(app, tmp_path, lambda body: body)
    c = TestClient(app)
    for url in ['/api/addons', '/api/addons/sample/assets/client.js']:
        assert c.get(url).status_code == 401
    assert c.post('/api/addons/sample/echo', json={}).status_code == 401
    key = {'X-Studio-Key': server.ACCESS_KEY}
    assert c.get('/api/addons', headers=key).status_code == 200
    assert c.post('/api/addons/sample/echo', json={'x': 1}, headers=key).json() == {'x': 1}
    assert c.post('/api/addons/sample/echo', json={}, headers={**key, 'Origin': 'https://example.invalid'}).status_code == 403


def test_bad_addon_does_not_partly_register_agents(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ADDONS", "1")
    folder(tmp_path, '''def register(addon):
    addon.agent("sample-agent", "Sample", "sample", ask=lambda **kw: "answer")
    addon.agent("claude", "Replacement", "bad", ask=lambda **kw: "bad")
''')
    assert client(tmp_path).get('/api/addons').json() == []
    assert 'sample-agent' not in agents.CLIS
    assert agents.CLIS['claude'][1] == 'claude'


def test_websocket_routes_are_not_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ADDONS", "1")
    folder(tmp_path, '''def register(addon):
    @addon.router.websocket("/ws")
    async def websocket(ws): pass
''')
    assert client(tmp_path).get('/api/addons').json() == []


def test_harness_runs_in_existing_task_worker_with_mcp_and_session(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ADDONS", "1")
    executable = tmp_path / 'sample-cli'
    executable.write_text('#!' + sys.executable + '\nimport json,sys\nsys.stdin.read()\nprint(json.dumps({"answer":"done", "session":"session-1"}))\n')
    executable.chmod(0o755)
    folder(tmp_path, '''import json
def ask(**kw): return "brain:" + kw["prompt"]
def argv(**kw):
    if kw["task"]["prompt"] == "again": assert kw["session"] == "session-1"
    assert "studio" in kw["servers"]
    assert kw["config_path"].endswith("mcp.json")
    return [kw["tool"]]
def read(line, task):
    event = json.loads(line)
    task["reply"] = event["answer"]
    task["_session"]["id"] = event["session"]
    return event["answer"] + "\\n"
def register(addon):
    addon.agent("sample-agent", "Sample agent", "sample-cli", ask=ask, argv=argv, read=read, session=True)
''')
    c = client(tmp_path)
    assert c.get('/api/addons').json()[0]['agents'][0]['harness'] is True
    monkeypatch.setattr(agents, 'HOME', tmp_path)
    monkeypatch.setattr(agents, 'cli', lambda name: str(executable))
    monkeypatch.setattr(agents, 'mcp_servers', lambda *args: {'studio': {'command': 'sample'}})
    monkeypatch.setattr(agents, 'saved_settings', lambda: {'remote': {'host': '', 'backends': []}, 'agent': '', 'default': ''})
    assert agents.ask('sample-agent', '', 'hello') == 'brain:hello'
    thread = agents.new_thread('sample-agent', ['studio'], 'Test', True)
    task = agents.submit({'thread': thread['id'], 'prompt': 'hello'})
    ends = time.monotonic() + 5
    while agents.tasks[task['id']]['status'] in ('queued', 'running') and time.monotonic() < ends:
        time.sleep(0.01)
    done = agents.tasks[task['id']]
    assert done['status'] == 'done' and done['reply'] == 'done'
    assert done['_session']['id'] == 'session-1'
    assert json.loads((agents.work_folder(done) / 'mcp.json').read_text())['mcpServers']['studio']['command'] == 'sample'
    again = agents.submit({'thread': thread['id'], 'prompt': 'again'})
    ends = time.monotonic() + 5
    while agents.tasks[again['id']]['status'] in ('queued', 'running') and time.monotonic() < ends:
        time.sleep(0.01)
    second = agents.tasks[again['id']]
    assert second['status'] == 'done'
    assert agents.work_folder(second) == agents.work_folder(done)
    assert json.loads((agents.work_folder(second) / 'mcp.json').read_text())['mcpServers']['studio']['command'] == 'sample'
    assert agents.thread_of(thread['id'])['session']['id'] == 'session-1'



def test_frontend_registration_and_duplicate_rollback():
    from test_shell_page import run
    out = run('''
      const before = ROOMS.length;
      StudioAddons.register({rooms:[{id:"sample",name:"Sample",group:"engines",tabs:["Sample"]}],
        tabs:[{id:"Sample",room:"sample",label:"My sample",view:()=>el("div",{},"ready")}]});
      out.room = roomOf("Sample").id; out.view = text(StudioAddons.view("Sample")());
      try { StudioAddons.register({rooms:[{id:"sample",name:"Oops",group:"engines",tabs:["Other"]}]}); } catch { out.refused = true; }
      out.added = ROOMS.length - before; out.tab = TAB_NAMES.Sample;
    ''')
    assert out == {'room': 'sample', 'view': 'ready', 'refused': True, 'added': 1, 'tab': 'My sample'}


def test_symlink_root_does_not_execute_code(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ADDONS", "1")
    outside = tmp_path / "outside"
    marker = tmp_path / 'executed'
    folder(outside, f'from pathlib import Path\nPath({str(marker)!r}).touch()', '/* outside */')
    home = tmp_path / "home"
    home.mkdir()
    (home / "addons").symlink_to(outside / "addons", target_is_directory=True)
    assert client(home).get('/api/addons').json() == []
    assert not marker.exists()


def test_new_room_cannot_claim_builtin_tab():
    from test_shell_page import run
    out = run('''const before = ROOMS.length;
      try { StudioAddons.register({rooms:[{id:"steal",name:"Steal",group:"engines",tabs:["Home"]}]}); } catch { out.refused = true; }
      out.unchanged = ROOMS.length === before;''')
    assert out == {'refused': True, 'unchanged': True}


def test_addon_connect_can_check_then_become_default():
    from test_shell_page import run, CONNECT
    out = run(CONNECT + '''
      const b = {id:"sample-agent",label:"Sample",kind:"cli",found:true,where:"here",addon:true,signin_flow:"terminal"};
      BR.data = {default:"local",settings:{},backends:[b]}; BR.tools = {tools:[]};
      answers["/api/brains/sample-agent/connect"] = base("sample-agent", {signin:{state:"unknown",detail:"Use sample-cli login, then Check."}});
      await connectStart(b); await settle();
      out.initial = steps("sample-agent"); out.instructions = text(panel("sample-agent"));
      out.noEmptyCopy = !button(panel("sample-agent"), "Copy");
      answers["/api/brains/sample-agent/verify"] = {ok:true,text:"ok",elapsedMs:120};
      answers["/api/brains/sample-agent/connect"] = base("sample-agent", {signin:{state:"signed_in"},verify:{ok:true,text:"ok"}});
      answers["/api/brains/sample-agent/default"] = {settings:{default:"sample-agent",agent:"sample-agent",remote:{host:"",backends:[]}}};
      await button(panel("sample-agent"), "Check").attrs.onclick(); await settle();
      out.ready = steps("sample-agent"); out.default = BR.data.default;
      out.noSigninRequest = !calls.some(c=>c[0] === "/api/brains/sample-agent/signin");
    ''')
    assert out['initial']['verify'] == 'wait'
    assert 'sample-cli login' in out['instructions'] and out['noEmptyCopy']
    assert out['ready']['signin'] == out['ready']['verify'] == 'done'
    assert out['default'] == 'sample-agent' and out['noSigninRequest']


@pytest.mark.parametrize("value", [123, {}, "  "])
def test_bad_agent_command_does_not_register(tmp_path, monkeypatch, value):
    monkeypatch.setenv("STUDIO_ADDONS", "1")
    folder(tmp_path, f'def register(addon):\n    addon.agent("sample-agent", "Sample", {value!r}, ask=lambda **kw: "ok")')
    assert client(tmp_path).get('/api/addons').json() == []
    assert 'sample-agent' not in agents.CLIS
