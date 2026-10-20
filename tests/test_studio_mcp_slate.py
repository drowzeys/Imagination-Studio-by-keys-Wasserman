"""The Studio MCP server's Slate tools and wait_for_jobs (mcp/studio-mcp.py), against the app's own routes through
FastAPI's test client: python3 -m pytest tests/test_studio_mcp_slate.py. A queued job is recorded, never run."""
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
spec = importlib.util.spec_from_file_location("studio_mcp", ROOT / "mcp/studio-mcp.py")
mcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcp)


@pytest.fixture
def studio(monkeypatch):
    queued, states = [], {}

    def call(method, path, body=None, raw=None, kind="application/json"):
        if method == "POST" and path == "/api/jobs":  # recorded, not run
            job = {"id": f"job{len(queued) + 1}", "kind": body["kind"], "status": "queued", "title": body["title"], "params": body["params"], "outputs": []}
            queued.append(job)
            return job
        if method == "GET" and path.startswith("/api/jobs/"):
            job_id = path.rsplit("/", 1)[1]
            states[job_id] = states.get(job_id, 0) + 1
            return {"id": job_id, "kind": "scout", "status": "done" if states[job_id] > 1 else "running", "outputs": ["scouts/x/a.png"]}
        answer = client.request(method, path, json=body)
        if answer.status_code >= 400:
            raise mcp.StudioError(f"the Studio said: {answer.json().get('error')} ({answer.status_code})")
        return answer.json()
    monkeypatch.setattr(mcp, "call", call)
    monkeypatch.setattr(mcp.time, "sleep", lambda s: None)
    return queued


def tool(tool_name, **args):
    reply = mcp.answer({"method": "tools/call", "params": {"name": tool_name, "arguments": args}})
    assert not reply.get("isError"), reply
    return json.loads(reply["content"][0]["text"])


def test_a_scene_of_shots_is_laid_out_and_a_shot_rendered(studio):
    film = tool("slate_new_film", name="Lighthouse", logline="A keeper at dusk.")
    scene = tool("slate_add_scene", film="lighthouse", name="Dusk", synopsis="The lamp is lit.")
    shots = [tool("slate_add_shot", film=film["film"], scene="Dusk", name=f"Shot 0{i}", prompt=f"# Subject: The keeper, beat {i}.\n# Mood: calm",
                  seconds=5, size="WS", movement="locked") for i in (1, 2, 3)]
    assert shots[0]["rendered"] is False and "not rendered" in shots[0]["note"]
    saved = client.get(f"/api/slate/projects/{film['film']}").json()
    assert saved["logline"] == "A keeper at dusk." and [s["name"] for s in saved["scenes"]] == ["Dusk"]
    shot = saved["scenes"][0]["shots"][0]
    assert scene["scene"] == saved["scenes"][0]["id"] and shot["id"] == shots[0]["shot"] and shot["spec"]["durationSec"] == 5
    assert shot["spec"]["size"] == "WS" and shot["tf"]["start"] == "text" and len(saved["scenes"][0]["shots"]) == 3
    assert shot["prompt"] == "# Subject\nThe keeper, beat 1.\n# Mood\ncalm"  # a heading with its text on one line is split as Slate reads it
    # the page's own compile, then the queue: the take goes into the film's takes folder and is logged in the shot
    job = tool("slate_render_shot", film=film["film"], shot="Shot 02")
    params = studio[0]["params"]
    assert job["id"] == "job1" and job["take"].startswith(f"slate/{film['film']}/takes/{shots[1]['shot']}-") and job["take"].endswith(".mp4")
    assert params["output"] == job["take"] and params["slate"]["shot"] == shots[1]["shot"] and params["frames"] == 124 and "keeper" in params["prompt"]
    takes = tool("slate_film", film=film["film"])["scenes"][0]["shots"][1]["takes"]
    assert takes == [{"path": job["take"], "job": "job1", "rating": "good"}]
    # a first frame that is not a finished picture is refused, not rendered from text
    reply = mcp.answer({"method": "tools/call", "params": {"name": "slate_render_shot", "arguments": {"film": film["film"], "shot": "Shot 01",
                                                                                                         "first_frame": "scouts/none.png"}}})
    assert reply["isError"] and "not a finished picture" in reply["content"][0]["text"] and len(studio) == 1
    # a name that is not there says so
    reply = mcp.answer({"method": "tools/call", "params": {"name": "slate_add_shot", "arguments": {"film": film["film"], "scene": "Night", "name": "A", "prompt": "x"}}})
    assert reply["isError"] and "no scene called Night" in reply["content"][0]["text"]


def test_wait_for_jobs_returns_when_they_are_finished(studio):
    done = tool("wait_for_jobs", ids=["a", "b"], seconds=30)
    assert [j["status"] for j in done] == ["done", "done"] and done[0]["outputs"] == ["scouts/x/a.png"]
