"""app/prompt_builder.py: python3 -m pytest tests/test_prompt_builder.py. No GPU, no network, no brain.
The Prompt Builder writes an H3 prompt from the user's own cast and a few choices; the Create page imports it."""
import os
import re
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import prompt_builder as B  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

here = TestClient(server.app, client=("127.0.0.1", 50000), base_url="http://127.0.0.1")


def test_a_new_studio_has_the_starter_cast_and_keeps_its_own():
    B.cast_file().unlink(missing_ok=True)
    cast = here.get("/api/builder/cast").json()
    assert [one["name"] for one in cast] == [one["name"] for one in B.STARTER_CAST]
    mine = cast[:2] + [{"name": "Ana Reyes", "look": "a tall woman", "wardrobe": "a red coat", "voice": "a husky voice", "era": "nonsense"}]
    saved = here.put("/api/builder/cast", json=mine).json()
    assert [one["name"] for one in saved][-1] == "Ana Reyes" and saved[-1]["era"] == "" and saved[-1]["id"]
    assert here.get("/api/builder/cast").json() == saved
    assert here.put("/api/builder/cast", json=[{"look": "no name"}]).status_code == 400


def test_the_layout_without_a_brain_is_what_create_imports():
    cast = here.put("/api/builder/cast", json=[dict(one) for one in B.STARTER_CAST[:2]]).json()
    r = here.post("/api/builder/write", json={"cast": [one["id"] for one in cast], "scenario": "A standoff",
                                               "location": "a roadside diner", "time": "night", "look": "90s-buddy-cop",
                                               "seconds": 25, "shots": "many", "idea": "They argue over the last slice of pie."}).json()
    assert r["by"] == "template" and not r["error"]
    found = r["found"]
    assert found["seconds"] == 25 and [d["speaker"] for d in found["dialogue"]] == ["Rex Callahan", "Dee Morales"] * 2 + ["Rex Callahan"]
    assert found["music"] == "N/A" and "roadside diner" in found["soundscape"]
    f = r["fields"]
    assert f["scene"].startswith("A roadside diner at night.") and "Rex Callahan and Dee Morales are in the frame" in f["scene"]
    # each character's look goes with the scene, which every clip of a long video reads
    assert "Rex Callahan: a broad-shouldered man" in f["scene"] and "Dee Morales: a lean woman" in f["scene"]
    assert f["action"].startswith("They argue over the last slice of pie.") and f["camera"] == "Fixed camera."
    assert len(f["beats"]) == 2  # shots starting at 0, 5, 10 s and 15, 20 s: two 15 s clips


def test_a_written_prompt_gives_create_its_boxes():
    prompt = """A test, 20 seconds, 16:9, 24fps

integrated_multimodal_description:
A lantern-lit cellar, dusty bottles, warm amber light.

[Shot 1] 00:00-00:08
Camera movement: Slow dolly in.
Image: Mira lifts the lantern.
Dialogue (Mira, softly): "Who's there?"

[Shot 2] 00:08-00:16
Image: Oren steps out of the shadow.

[Shot 3] 00:16-00:20
Image: They both laugh.

overall_soundscape:
Dripping water."""
    f = here.post("/api/builder/fields", json={"prompt": prompt}).json()["fields"]
    assert f == {"scene": "A lantern-lit cellar, dusty bottles, warm amber light.", "camera": "Slow dolly in.",
                 "action": "Mira lifts the lantern. Then Oren steps out of the shadow. Then They both laugh.",
                 "beats": ["Mira lifts the lantern. Oren steps out of the shadow.", "They both laugh."]}


def test_the_brain_is_asked_for_the_users_own_cast_only(monkeypatch):
    import slate
    asked = {}
    monkeypatch.setattr(slate, "ask", lambda backend, system, prompt, tier, *a: asked.update(system=system, prompt=prompt) or "```\nA, 5 seconds\n\n[Shot 1] 00:00-00:05\nDialogue (Mira): \"Hi.\"\n```")
    cast = here.put("/api/builder/cast", json=[{"name": "Mira", "look": "a woman", "wardrobe": "grey", "voice": "soft", "era": "wuxia"}]).json()
    r = here.post("/api/builder/write", json={"brain": True, "cast": [cast[0]["id"]], "seconds": 5, "language": "Mandarin"}).json()
    assert r["by"] == "brain" and r["prompt"].startswith("A, 5 seconds") and r["found"]["dialogue"][0]["text"] == "Hi."
    assert "Use only the characters you are given" in asked["system"] and "real people" in asked["system"]
    assert "- Mira: a woman; wears grey; voice: soft; styled after a wuxia" in asked["prompt"] and "speak Mandarin" in asked["prompt"]
    assert "parts of 15 s" not in asked["prompt"]  # 5 s: one clip
    here.post("/api/builder/write", json={"brain": True, "cast": [cast[0]["id"]], "seconds": 30})
    assert "rendered as 2 parts of 15 s" in asked["prompt"] and "characters' faces" in asked["prompt"]
    # a brain that fails leaves the layout to fill in by hand, with its reason
    monkeypatch.setattr(slate, "ask", lambda *a: (_ for _ in ()).throw(RuntimeError("no brain is set up")))
    r = here.post("/api/builder/write", json={"brain": True, "cast": [cast[0]["id"]], "seconds": 5}).json()
    assert r["by"] == "template" and r["error"] == "no brain is set up" and "[Shot 1]" in r["prompt"]


def test_the_starter_looks_and_cast_are_the_studios_own():
    assert all(len(one["look"]) > 20 and one["voice"] for one in B.STARTER_CAST)
    assert set(B.options()["looks"]) == set(B.LOOKS) - {""}


def test_every_room_tab_is_one_the_page_opens():
    # go() ignores a tab the page's own TABS does not list: a menu entry missing there does nothing when clicked
    import json
    import re
    page = (ROOT / "app" / "static" / "index.html").read_text()
    tabs = set(json.loads(re.search(r"^const TABS = (\[.*?\]);", page, re.M).group(1)))
    shell = (ROOT / "app" / "static" / "shell.js").read_text()
    rooms = re.search(r"const ROOMS = \[(.*?)\n\];", shell, re.S).group(1)
    wanted = {tab for listed in re.findall(r"tabs: (\[[^\]]*\])", rooms) for tab in json.loads(listed)}
    assert "Prompt Builder" in wanted and wanted <= tabs, sorted(wanted - tabs)
    assert '"Prompt Builder": viewBuilder' in page and '<script src="builder.js"></script>' in page


def test_a_reference_sheet_is_a_still_then_its_turnaround(monkeypatch):
    queued, records = [], {}

    def fake_submit(body):
        job = {"id": f"job{len(queued)}", "kind": body["kind"], "params": body["params"], "status": "done"}
        job["outputs"] = (["scouts/job0/scout_s7.png", "scouts/job0/sheet.jpg"] if body["kind"] == "scout"
                          else ["scouts/job1/turn_sheet.png", "scouts/job1/turn_view1.png", "scouts/job1/turn_view2.png"])
        queued.append(body)
        records[job["id"]] = job
        return job

    monkeypatch.setattr(B, "submit", fake_submit)
    monkeypatch.setattr(B, "lookup", records.get)
    monkeypatch.setattr(B, "SHEET_POLL_S", 0)
    cast = here.put("/api/builder/cast", json=[dict(one) for one in B.STARTER_CAST[1:2]]).json()
    cid = cast[0]["id"]
    assert here.post(f"/api/builder/cast/{cid}/sheet").json() == {"job": "job0"}
    for _ in range(200):
        sheet = here.get("/api/builder/cast").json()[0].get("sheet") or {}
        if sheet.get("state") in ("done", "failed"):
            break
        import time
        time.sleep(0.02)
    assert sheet["state"] == "done", sheet
    assert sheet["still"] == "scouts/job0/scout_s7.png" and sheet["sheet"] == "scouts/job1/turn_sheet.png"
    assert sheet["views"] == ["scouts/job1/turn_view1.png", "scouts/job1/turn_view2.png"]
    still, turn = queued
    assert still["kind"] == "scout" and "Full-body photograph of a lean woman" in still["params"]["prompt"]
    assert "brown leather jacket" in still["params"]["prompt"] and "Plain light grey" in still["params"]["prompt"]
    assert turn == {"kind": "turnaround", "title": "Reference sheet: Dee Morales",
                    "params": {"image": "scouts/job0/scout_s7.png", "size": 1024, "strength": 1, "seed": 0}}
    # the page saving an older copy of the cast keeps the sheet; a picture outside the Studio's own is not taken
    saved = here.put("/api/builder/cast", json=[{**cast[0], "sheet": {"still": "/etc/passwd.png"}}]).json()
    assert saved[0]["sheet"]["sheet"] == "scouts/job1/turn_sheet.png"
    assert "sheet" not in B.clean_character({"name": "X", "sheet": {"still": "../../x.png"}})
    assert here.post("/api/builder/cast/nobody/sheet").status_code == 404


def test_more_looks_and_scenarios_are_offered():
    o = B.options()
    assert len(o["scenarios"]) >= 35 and len(o["locations"]) >= 40 and len(o["looks"]) >= 30
    assert "A double-cross" in o["scenarios"] and "80s-horror" in o["looks"] and "a lighthouse in a storm" in o["locations"]


def clip_record(prompt, monkeypatch, tmp_path_factory=None):
    import shutil
    import subprocess
    clip = server.HOME / "renders" / "test-clip.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("ffmpeg") and not clip.is_file():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=96x64:rate=24:duration=2",
                        "-pix_fmt", "yuv420p", str(clip)], check=True, timeout=60)
    job = {"id": "clip1", "kind": "video", "status": "done", "outputs": ["renders/test-clip.mp4"], "title": "Diner",
           "params": {"prompt": prompt}}
    monkeypatch.setattr(B, "lookup", {"clip1": job}.get)
    return job


def test_a_character_from_a_clip_made_here(monkeypatch):
    import shutil
    prompt = server.compose({"scene": "A diner at night. Rex Callahan: a broad-shouldered man, wearing a denim jacket. "
                                      "Dee Morales: a lean woman; a leather jacket.",
                             "dialogue": [{"voice": "a deep, gravelly voice", "text": "Tonight.", "speaker": "Rex Callahan"},
                                          {"voice": "a fast, wry voice", "text": "Cute.", "speaker": "Dee Morales"},
                                          {"voice": "a deep, gravelly voice", "text": "Again.", "speaker": "Rex Callahan"}]}, False)
    clip_record(prompt, monkeypatch)
    r = here.get("/api/builder/from-clip/clip1").json()
    assert r["clip"] == "renders/test-clip.mp4"
    assert r["characters"] == [
        {"name": "Rex Callahan", "look": "a broad-shouldered man", "wardrobe": "a denim jacket", "voice": "a deep, gravelly voice"},
        {"name": "Dee Morales", "look": "a lean woman", "wardrobe": "a leather jacket", "voice": "a fast, wry voice"}]
    assert here.get("/api/builder/from-clip/nothing").status_code == 404
    cast = here.put("/api/builder/cast", json=[{"name": "Rex Callahan", "look": "x", "wardrobe": "", "voice": ""}]).json()
    if not shutil.which("ffmpeg"):
        return
    made = here.post(f"/api/builder/cast/{cast[0]['id']}/frame", json={"job": "clip1", "at": 1, "part": "left"}).json()
    assert re.fullmatch(r"scouts/cast-[0-9a-f]{10}/frame\.png", made["picture"]) and (server.HOME / made["picture"]).is_file()
    assert here.get("/api/builder/cast").json()[0]["picture"] == made["picture"]
    assert here.post(f"/api/builder/cast/{cast[0]['id']}/frame", json={"job": "clip1", "part": "top"}).status_code == 400


def test_a_sheet_from_a_characters_own_picture_puts_them_on_grey_first(monkeypatch):
    queued = []
    monkeypatch.setattr(B, "submit", lambda body: queued.append(body) or {"id": "j"})
    monkeypatch.setattr(B, "lookup", lambda job_id: None)
    monkeypatch.setattr(B.threading, "Thread", lambda **k: type("T", (), {"start": lambda self: None})())
    cast = here.put("/api/builder/cast", json=[{"name": "Rex", "look": "a man", "picture": "scouts/cast-abc/frame.png"}]).json()
    here.post(f"/api/builder/cast/{cast[0]['id']}/sheet")
    assert queued[0]["kind"] == "edit" and queued[0]["params"]["image"] == "scouts/cast-abc/frame.png"
    assert "plain light grey" in queued[0]["params"]["prompt"] and queued[0]["params"]["image_model"] == "qwen"
