"""The Studio MCP server's tools for the features beyond stills, clips, voice and songs (mcp/studio-mcp.py): edits,
long takes, Ref2VA references, sound effects and foley, a song under a clip, the QA gate, Motion Previs and cancelling
a job. python3 -m pytest tests/test_studio_mcp_tools.py. Each call the server would make to the Studio is recorded and
answered here: nothing is queued or run."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("studio_mcp_tools", ROOT / "mcp/studio-mcp.py")
mcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcp)


@pytest.fixture
def studio(monkeypatch):
    sent = []

    def call(method, path, body=None, raw=None, kind="application/json"):
        sent.append((method, path, body))
        if path == "/api/state":
            return {"turbo": True}
        if path.startswith("/api/probe"):
            return {"duration": None if "missing" in path else 8.5}
        if path == "/api/projects":
            return [{"id": "p1"}]
        if path == "/api/projects/p1":
            return {"id": "p1", "name": "Cut", "timeline": []}
        if path.startswith("/api/jobs/missing"):
            raise mcp.StudioError("the Studio said: no such job (404)")
        if path.startswith("/api/motion/jobs/"):
            return {"id": path.split("/")[4], "status": "done", "pack": path.split("/")[4], "stages": {}, "title": "walk.mp4"}
        if method == "POST" and path == "/api/motion/jobs":
            return {"id": "m1", "status": "queued", "pack": None, "stages": {}, "title": "walk.mp4", "params": body}
        if method == "POST" and path.endswith("/cancel"):
            return {"id": path.split("/")[3], "kind": "video", "status": "cancelled", "outputs": []}
        kind = (body or {}).get("kind") or {"/api/music/soundtrack": "soundtrack", "/api/qa": "qa"}.get(path, "job")
        job = {"id": f"j{len(sent)}", "kind": kind, "status": "queued", "outputs": [], "params": (body or {}).get("params") or body}
        if path == "/api/qa":
            job["qa"] = {"passed": True}
        return job
    monkeypatch.setattr(mcp, "call", call)
    return sent


def tool(name, **args):
    reply = mcp.answer({"method": "tools/call", "params": {"name": name, "arguments": args}})
    assert not reply.get("isError"), reply
    return json.loads(reply["content"][0]["text"])


def refused(name, **args) -> str:
    reply = mcp.answer({"method": "tools/call", "params": {"name": name, "arguments": args}})
    assert reply.get("isError"), reply
    return reply["content"][0]["text"]


def test_every_tool_is_listed_with_its_required_arguments():
    listed = {t["name"]: t for t in mcp.answer({"method": "tools/list"})["tools"]}
    for name, required in (("queue_edit", ["image", "prompt"]), ("queue_long", ["prompt", "total_seconds"]), ("queue_sound", ["kind"]),
                           ("queue_soundtrack", ["song", "video"]), ("check_clip", ["path"]), ("motion_previs", ["clip"]), ("cancel_job", ["id"])):
        assert listed[name]["inputSchema"]["required"] == required
    assert "references" in listed["queue_clip"]["inputSchema"]["properties"]


def test_edits_long_takes_and_references_go_to_the_queue(studio):
    tool("queue_edit", image="uploads/street.png", prompt="night, rain on the street", image_model="")
    assert studio[-1] == ("POST", "/api/jobs", {"kind": "edit", "title": "agent: edit", "params": {"image": "uploads/street.png", "prompt": "night, rain on the street"}})
    tool("queue_long", prompt="a train in the desert", total_seconds=30, preset="small", first_frame="scouts/a/s1.png", beats=["dawn", "noon"])
    assert studio[-1][2] == {"kind": "long", "title": "agent: long take", "params": {"prompt": "a train in the desert", "total_seconds": 30,
                                                                                     "beats": ["dawn", "noon"], "preset": "small", "image": "scouts/a/s1.png"}}
    tool("queue_clip", prompt="<Picture 1> and <Picture 2> walk", references=["uploads/hero.png", "uploads/robot.png"], seconds=5)
    params = studio[-1][2]["params"]
    assert studio[-1][2]["kind"] == "video" and params["references"] == ["uploads/hero.png", "uploads/robot.png"] and "quality" not in params
    assert "first_frame" in refused("queue_clip", prompt="x", references=["uploads/hero.png"], first_frame="uploads/street.png")


def test_sounds_a_song_under_a_clip_and_the_qa_gate(studio):
    tool("queue_sound", kind="sfx", prompt="a door creaks", seconds=4, video="renders/a.mp4")
    assert studio[-1][2] == {"kind": "sfx", "params": {"prompt": "a door creaks", "seconds": 4}}  # an effect takes no clip
    tool("queue_sound", kind="foley", video="renders/walk.mp4", prompt="gravel steps", seconds=3)
    assert studio[-1][2] == {"kind": "foley", "params": {"prompt": "gravel steps", "video": "renders/walk.mp4"}}
    assert "kind is sfx" in refused("queue_sound", kind="music", prompt="x")
    tool("queue_soundtrack", song="0101-song", video="renders/walk.mp4", music_db=-12)
    assert studio[-1] == ("POST", "/api/music/soundtrack", {"take": "0101-song", "video": "renders/walk.mp4", "music_db": -12})
    checked = tool("check_clip", path="renders/walk.mp4")
    assert studio[-1] == ("POST", "/api/qa", {"path": "renders/walk.mp4"}) and checked["kind"] == "qa" and checked["qa"] == {"passed": True}


def test_motion_previs_takes_the_clip_to_its_end_and_is_followed_like_a_job(studio):
    queued = tool("motion_previs", clip="renders/walk.mp4", mode="camera-only")
    assert studio[-1] == ("POST", "/api/motion/jobs", {"source": "renders/walk.mp4", "start": 0.0, "end": 8.5, "mode": "camera-only"})
    assert queued["kind"] == "motion" and queued["id"] == "m1"
    # an id the render queue does not know is a Motion Previs job: job_status and wait_for_jobs find it there
    assert tool("job_status", id="missing")["status"] == "done"
    assert tool("wait_for_jobs", ids=["missing"])[0]["pack"] == "missing"
    assert tool("cancel_job", id="j9")["status"] == "cancelled" and studio[-1][1] == "/api/jobs/j9/cancel"


def test_a_clip_that_is_not_in_the_library_is_not_put_on_a_timeline(studio):
    said = refused("add_to_timeline", path="/Users/me/ImaginationStudio/agent/th-1/renders/missing.mp4")
    assert "no finished clip at" in said and not any(method == "PUT" for method, _, _ in studio)


def test_a_still_is_one_picture_unless_more_are_asked_for(studio):
    tool("queue_still", prompt="a neon street in the rain")
    assert studio[-1][2]["params"] == {"prompt": "a neon street in the rain", "count": 1}
    tool("queue_still", prompt="poster options", count=4)
    assert studio[-1][2]["params"]["count"] == 4
