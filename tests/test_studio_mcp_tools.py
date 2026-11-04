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


def test_every_tool_is_listed_with_its_required_arguments(studio):
    listed = {t["name"]: t for t in mcp.answer({"method": "tools/list"})["tools"]}
    for name, required in (("queue_edit", ["image", "prompt"]), ("queue_long", ["prompt", "total_seconds"]), ("queue_sound", ["kind"]),
                           ("queue_soundtrack", ["song", "video"]), ("check_clip", ["path"]), ("motion_previs", ["clip"]), ("cancel_job", ["id"])):
        assert listed[name]["inputSchema"]["required"] == required
    assert "references" in listed["queue_clip"]["inputSchema"]["properties"]


def test_windows_tools_advertise_only_qualified_routes_and_limits(monkeypatch):
    monkeypatch.setattr(mcp, "call", lambda *args: {"platform": "windows", "capabilities": {
        "scout": {"available": True}, "sfx": {"available": True}, "graphics": {"available": True,
            "templates": ["title", "lower-third", "kinetic", "slideshow", "music-video", "explainer"]},
        "beats": {"available": True},
        "export": {"available": False}}})
    tools = {t["name"]: t for t in mcp.answer({"method": "tools/list"})["tools"]}
    assert {"studio_state", "slate_add_shot", "queue_still", "queue_sound", "make_graphics", "music_beats"} <= tools.keys()
    assert not {"queue_clip", "queue_voice", "queue_song", "queue_edit", "check_clip", "train_lora", "export_timeline"} & tools.keys()
    assert tools["queue_sound"]["inputSchema"]["properties"]["seconds"]["const"] == 5
    assert tools["queue_sound"]["inputSchema"]["properties"]["kind"]["enum"] == ["sfx"]
    assert tools["make_graphics"]["inputSchema"]["properties"]["template"]["enum"] == ["title", "lower-third", "kinetic", "slideshow", "music-video", "explainer"]
    assert next(t for t in mcp.TOOLS if t["name"] == "queue_sound")["inputSchema"]["properties"]["kind"]["enum"] == ["sfx", "foley"]


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


def test_a_cut_is_finished_and_exported_for_premiere_or_after_effects(studio):
    tool("finish_cut", clips=["renders/a.mp4", "renders/b.mp4"], song="none", title="Night Drive", mix="music", fps=30)
    assert studio[-1] == ("POST", "/api/jobs", {"kind": "finish", "title": "Finish: 2 clips", "params": {
        "mix": "music", "fps": 30, "clips": [{"path": "renders/a.mp4"}, {"path": "renders/b.mp4"}], "song": None,
        "title": {"text": "Night Drive", "subtitle": ""}}})
    assert "is empty" in refused("finish_cut", project="p1")
    tool("export_cut", id="0101-abc", to="aftereffects")
    assert studio[-1] == ("POST", "/api/finish/0101-abc/convert", {"to": "aftereffects"})
    assert "premiere or aftereffects" in refused("export_cut", id="0101-abc", to="resolve")


def test_upload_file_publishes_only_a_file_of_the_studio_folder(tmp_path, monkeypatch, studio):
    """The upload is listed in the Library and downloadable from every device of the studio: the agent -- whose
    judgement is steerable by whatever a web page or a tool server tells it -- must not be able to post the
    person's private files through it. Only a real file of the studio folder, never a hidden one, goes up."""

    home = tmp_path / "ImaginationStudio"
    (home / "agent" / "thread1").mkdir(parents=True)
    (home / "agent" / "thread1" / "blend.png").write_bytes(b"png made by blender")
    (home / ".secrets").mkdir()
    (home / ".secrets" / "id_ed25519").write_bytes(b"a key in the studio folder")
    private = tmp_path / "id_ed25519"
    private.write_bytes(b"a key outside it")
    sneaky = home / "sneaky.png"
    try:
        sneaky.symlink_to(private)  # a path inside the folder that resolves out of it
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("File symlinks require a Windows privilege not enabled on this machine")
        raise
    monkeypatch.setenv("STUDIO_HOME", str(home))
    assert tool("upload_file", path=str(home / "agent" / "thread1" / "blend.png"))["kind"] == "job"  # the ordinary case
    assert "not a file of the studio folder" in refused("upload_file", path=str(private))
    assert "hidden" in refused("upload_file", path=str(home / ".secrets" / "id_ed25519"))
    assert "not a file of the studio folder" in refused("upload_file", path=str(sneaky))
