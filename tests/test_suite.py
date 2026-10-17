"""The filmmaker suite's files in and out: python3 -m pytest tests/test_suite.py. Every sample here is small and
synthetic, built in the layout the app that writes it uses."""
import io
import json
import os
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
os.environ.setdefault("STUDIO_GPU_LOCK", str(Path(tempfile.mkdtemp()) / "studio-gpu.lock.d"))
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402
import suite  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
HOME = server.HOME
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\r"
       b"IDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def zipped(files: dict) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as bundle:
        for name, body in files.items():
            bundle.writestr(name, body if isinstance(body, bytes) else body.encode() if isinstance(body, str) else json.dumps(body))
    return data.getvalue()


def send(name: str, body: bytes, app: str = "", **headers):
    return client.post("/api/suite/import", files={"file": (name, body)}, data={"app": app} if app else {}, headers=headers)


FOUNTAIN = """Title: The Last Reel
Author: Test

INT. PROJECTION BOOTH - NIGHT

MARA threads the film, hands shaking.

MARA
(whispering)
It has to run tonight.

JONAH (O.S.)
It won't.

EXT. ALLEY - CONTINUOUS

Rain hammers the dumpsters. Jonah waits.
"""


def test_the_suite_lists_its_apps():
    apps = client.get("/api/suite").json()
    ids = {one["id"] for one in apps}
    assert {"scriptbreak", "cork-board", "master-canvas", "storyboard-reference", "blockout", "slate", "circle-take", "call-sheet"} <= ids
    assert all(one["name"] and one["what"] and isinstance(one["imports"], list) and isinstance(one["exports"], list) for one in apps)


def test_a_fountain_screenplay_becomes_shots_cast_and_places():
    found = send("reel.fountain", FOUNTAIN.encode()).json()
    assert found["app"] == "fountain" and found["title"] == "The Last Reel"
    assert [one["scene"] for one in found["shots"]] == ["INT. PROJECTION BOOTH - NIGHT", "EXT. ALLEY - CONTINUOUS"]
    assert found["shots"][0]["dialogue"] == [{"character": "Mara", "line": "It has to run tonight."}, {"character": "Jonah", "line": "It won't."}]
    assert {one["name"] for one in found["cast"]} == {"Mara", "Jonah"}
    assert {one["name"] for one in found["locations"]} == {"Projection Booth", "Alley"}
    assert (HOME / "uploads" / "suite" / found["id"] / "import.json").is_file()
    assert found["id"] in {one["id"] for one in client.get("/api/suite/imports").json()}
    # and as a Slate film
    made = client.post(f"/api/suite/imports/{found['id']}/slate", json={}).json()
    film = client.get(f"/api/slate/projects/{made['project']}").json()
    assert [scene["name"] for scene in film["scenes"]] == ["INT. PROJECTION BOOTH - NIGHT", "EXT. ALLEY - CONTINUOUS"]
    first = film["scenes"][0]["shots"][0]
    names = {sheet["id"]: sheet["name"] for sheet in film["characters"]}
    assert first["prompt"].startswith("# Subject\n") and {names[i] for i in first["tf"]["cast"]} == {"Mara", "Jonah"}
    assert [line["text"] for line in first["tf"]["lines"]] == ["It has to run tonight.", "It won't."]
    assert first["tf"]["location"] == next(loc["id"] for loc in film["locations"] if loc["name"] == "Projection Booth")
    compiled = client.post("/api/slate/compile", json={"projectId": made["project"], "shotId": first["id"]}).json()
    assert "It has to run tonight." in compiled["prompt"]


def test_a_zip_that_reaches_outside_its_folder_is_refused():
    for bad in ("../escape.png", "/abs/escape.png", "a/../../escape.png", "C:/escape.png"):
        answer = send("x.zip", zipped({"prompts.json": [], bad: PNG}))
        assert answer.status_code == 400 and "outside" in answer.json()["error"], bad
    assert not (HOME / "uploads" / "escape.png").exists() and not (HOME / "uploads" / "suite" / "escape.png").exists()
    # a link inside a ZIP is refused too
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as bundle:
        link = zipfile.ZipInfo("still.png")
        link.external_attr = (0o120777 << 16)
        bundle.writestr(link, "/etc/passwd")
    assert send("x.zip", data.getvalue()).status_code == 400


def test_imports_come_from_this_computer_and_the_studio_page():
    assert send("reel.fountain", FOUNTAIN.encode(), Origin="http://evil.example").status_code == 403
    assert send("reel.fountain", FOUNTAIN.encode(), **{"Sec-Fetch-Site": "cross-site"}).status_code == 403
    found = send("reel.fountain", FOUNTAIN.encode()).json()
    assert client.post(f"/api/suite/imports/{found['id']}/slate", content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post("/api/suite/imports/..%2F..%2Fetc/slate", json={}).status_code in (400, 404, 405)
    assert client.post("/api/suite/imports/bad.id/slate", json={}).status_code == 400


def make_timeline() -> str:
    for name in ("one", "two"):
        (HOME / "renders" / f"{name}.mp4").write_bytes(b"not really a clip")
    project = client.post("/api/projects", json={"name": "Reel & <test>"}).json()
    project["timeline"] = [{"path": "renders/one.mp4", "in": 1.0, "out": 3.5, "duration": 5.125},
                           {"path": "renders/two.mp4", "in": 0, "out": 0, "duration": 5.125}]
    client.put(f"/api/projects/{project['id']}", json=project)
    return project["id"]


def test_a_timeline_as_fcpxml():
    project = make_timeline()
    answer = client.get(f"/api/export/timeline/{project}.fcpxml")
    assert answer.status_code == 200
    root = ET.fromstring(answer.content)
    assert root.tag == "fcpxml" and root.get("version") == "1.11"
    clips = root.findall("./library/event/project/sequence/spine/asset-clip")
    assert [clip.get("offset") for clip in clips] == ["0s", "60/24s"]
    assert [clip.get("start") for clip in clips] == ["24/24s", "0s"] and [clip.get("duration") for clip in clips] == ["60/24s", "123/24s"]
    sources = [rep.get("src") for rep in root.iter("media-rep")]
    assert all(src.startswith("file:///") and src.endswith((".mp4",)) for src in sources) and len(sources) == 2
    assert root.find("./library/event/project/sequence").get("duration") == "183/24s"
    assert client.get("/api/export/timeline/nope.fcpxml").status_code == 404


def test_a_timeline_with_its_media_opens_where_it_is_unzipped():
    project = make_timeline()
    answer = client.get(f"/api/export/timeline/{project}.zip", params={"root": "/Users/me/Movies/Cuts"})
    assert answer.status_code == 200
    bundle = zipfile.ZipFile(io.BytesIO(answer.content))
    names = set(bundle.namelist())
    assert {f"{project}/{project}.fcpxml", f"{project}/README.txt", f"{project}/media/one.mp4", f"{project}/media/two.mp4"} <= names
    sources = [rep.get("src") for rep in ET.fromstring(bundle.read(f"{project}/{project}.fcpxml")).iter("media-rep")]
    assert sources == [f"file:///Users/me/Movies/Cuts/{project}/media/one.mp4", f"file:///Users/me/Movies/Cuts/{project}/media/two.mp4"]
    # without a folder the clips are named beside the timeline, for relinking
    loose = zipfile.ZipFile(io.BytesIO(client.get(f"/api/export/timeline/{project}.zip").content))
    assert [rep.get("src") for rep in ET.fromstring(loose.read(f"{project}/{project}.fcpxml")).iter("media-rep")] == ["media/one.mp4", "media/two.mp4"]
    assert client.get(f"/api/export/timeline/{project}.zip", params={"root": "relative/folder"}).status_code == 400


def test_a_timeline_as_cmx_3600():
    project = make_timeline()
    text = client.get(f"/api/export/timeline/{project}.edl").text
    lines = text.split("\r\n")
    assert lines[0].startswith("TITLE: ") and lines[1] == "FCM: NON-DROP FRAME"
    events = [line for line in lines if line[:3].isdigit()]
    assert events[0].split() == ["001", "ONE", "AA/V", "C", "00:00:01:00", "00:00:03:12", "01:00:00:00", "01:00:02:12"]
    assert events[1].split()[-2:] == ["01:00:02:12", "01:00:07:15"]
    assert "* FROM CLIP NAME: two.mp4" in lines


SCRIPTBREAK = {"app": "scriptbreak", "version": 2, "saved": "2026-07-18T04:14:59.133Z", "project": ".", "draft": "Draft 2",
               "state": {"title": "AVA", "scriptName": "ava.fountain", "scenes": [
                   {"num": "1", "slug": "EXT. STREET (HOLLYWOOD, CA) - NIGHT", "intExt": "EXT", "location": "STREET", "master": "STREET", "sub": "",
                    "area": "HOLLYWOOD, CA", "tod": "NIGHT", "todBucket": "NIGHT", "eighths": 2, "page": 1, "elements": {"props": []},
                    "shots": [{"size": "EWS", "angle": "", "move": "Static", "lens": "", "desc": "Establishing — Street", "auto": True, "num": "1A"},
                              {"size": "MCU", "angle": "Eye level", "move": "Push in", "lens": "50mm", "desc": "Ava at the mic", "num": "1B"}],
                    "text": "AVA speaks.", "characters": ["AVA", "BARTENDER"], "synopsis": "Ava broadcasts into the night."}],
                   "bibles": {"characters": {"BARTENDER": {"desc": "A shirtless male bartender (18).", "auto": True}}, "locations": {}, "props": {}},
                   "look": {"aspect": "1.85:1"}, "customCats": [], "lookAuto": True}}

PACK_MD = """# AVA — AI Video Prompt Pack
*Generated by ScriptBreak (Wasserman Filmmaker Suite) · 2026-07-18 · scope: all 1 scenes*

> **HOW TO USE THIS FILE:** Paste this entire document into any AI assistant.

---

## YOUR TASK
1. Write one prompt per shot.

## CHARACTER BIBLE
- **AVA:** A voice on late-night radio.

## LOCATION BIBLE
- **STREET:** Wet asphalt, neon.

## SHOTS TO GENERATE
### Scene 1 — EXT. STREET - NIGHT  (2/8 pg)
Characters present: AVA
Action: Ava broadcasts into the night.
- **SHOT 1A** — EWS, Static: Establishing — Street
- **SHOT 1B** — MCU, Eye level, Push in, 50mm lens: Ava at the mic

---
*2 shots · 1 scenes · Prompt pack ends here. Begin with SHOT 1A.*
"""

CORK = {"schema": 1, "id": "proj_1a2b3c4d", "title": "AVA", "type": "feature",
        "boards": [{"id": "board_9f", "title": "AVA — Feature Wall", "columns": [
            {"id": "col_a1", "title": "Act I", "accent": "#287d8e", "collapsed": False, "cards": ["card_01", "card_02"]}]}],
        "activeBoardId": "board_9f",
        "cards": {"card_01": {"id": "card_01", "title": "Static", "synopsis": "Black screen, a voice through static.", "paper": "blue",
                              "intExt": "INT", "timeOfDay": "NIGHT", "locationId": "loc_st", "characterIds": ["char_ava"], "labelIds": [],
                              "status": "locked", "pages": 1.5, "due": "", "checklist": [], "arcNotes": {}, "notes": "Open cold."},
                  "card_02": {"id": "card_02", "title": "Cut card", "synopsis": "", "status": "cut", "characterIds": []},
                  "card_orphan": {"id": "card_orphan", "title": "Nobody points here"}},
        "characters": [{"id": "char_ava", "name": "Ava", "color": "#6d5a86", "role": "Deuteragonist", "actor": "", "want": "to be heard", "need": "", "arc": ""}],
        "locations": [{"id": "loc_st", "name": "KVOX Radio Station", "kind": "INT", "notes": "Cramped booth."}], "labels": []}

MASTER = {"id": "project_1", "title": "Neon Run", "view": {"x": 0, "y": 0, "scale": 1}, "continuity": {"characters": "", "locations": ""},
          "assets": [], "nodes": [
              {"id": "n1", "type": "scene", "title": "Opening", "x": 0, "y": 0},
              {"id": "n2", "type": "media", "title": "Scene 1 hero shot", "x": 300, "y": 100, "prompt": "A runner under neon.", "duration": "6s",
               "shotSize": "WS", "cameraMovement": "Tracking", "location": "Alley"},
              {"id": "n3", "type": "shot", "title": "Close", "x": 100, "y": 100, "subjectAction": "She looks back.", "duration": "4s"},
              {"id": "n4", "type": "character", "title": "Runner", "notes": "Silver jacket."}]}

HANDOFF_MANIFEST = {"schema": "master-canvas-handoff-v1", "title": "Neon Run", "projectId": "project_1", "exportedAt": "2026-08-01T00:00:00Z",
                    "continuity": {"characters": "Runner: silver jacket, short hair"},
                    "scenes": [{"sceneKey": "scene-01", "sceneNumber": 1, "title": "Scene 1", "shots": []}],
                    "shots": [{"id": "s2", "sceneKey": "scene-01", "orderLabel": "Scene1-02", "title": "Look back", "prompt": "She looks back.",
                               "duration": "4s", "globalOrder": 2, "sourcePath": "assets/scene-01/Scene1-02-look.png"},
                              {"id": "s1", "sceneKey": "scene-01", "orderLabel": "Scene1-01", "title": "Hero", "prompt": "A runner under neon.",
                               "duration": "6s", "globalOrder": 1, "shotSize": "WS", "sourcePath": "assets/scene-01/Scene1-01-hero.png"}],
                    "workflows": [], "references": [], "assets": []}

BOARD = {"project": "My Board", "exportedAt": "2026-07-07-06-16-09",
         "frames": [{"index": 1, "folder": "01_shot-1a-hero-enters", "label": "SHOT 1A — HERO ENTERS", "notes": "", "timeS": 12.48, "source": "clip.mp4",
                     "profileId": "midjourney", "crop": {"aspect": "2.39:1", "x": 0, "y": 0.12, "w": 1, "h": 0.75}, "prompt": "wide shot --ar 2.39:1"},
                    {"index": 2, "folder": "02_frame", "label": "", "notes": "close", "timeS": 0, "source": "still.png", "profileId": "flux", "crop": None,
                     "prompt": "a face in rain"}]}

BLOCKOUT_META = {"generator": {"app": "Blockout", "schema": 1}, "profile": {"id": "seedance-2", "name": "Seedance 2.0"},
                 "shot": {"name": "1A", "duration": 10.5, "fps": 24, "aspect": "16:9", "sensor": "fullFrame", "rig": "crane", "rigIntensity": 0, "seed": 807049658},
                 "cameraMarks": [{"index": 1, "time": 2, "hold": 2.5, "position": {"x": 9.2, "y": 1.4, "z": 10.8}, "panDeg": 40.3, "tiltDeg": -1.6, "focalLength": 35}],
                 "subjects": [{"name": "THIEF", "asset": "person.man", "labelColor": "#e5484d", "marks": [{"index": 1, "time": 0, "gait": "walk"}]}],
                 "warnings": []}

SLATE = {"id": "0869901b-aaaa", "name": "Impact", "logline": "One frame.", "world": "",
         "defaults": {"aspectRatio": "16:9", "fps": 24, "durationSec": 8, "targetModel": "seedance-2", "brain": "claude"},
         "scenes": [{"id": "scene-1", "name": "The hit", "synopsis": "", "shots": [
             {"id": "shot-msapabvd-wh6yw", "name": "Impact Frame", "intent": "Capture the exact instant.",
              "spec": {"durationSec": 8, "fps": 24, "aspectRatio": "16:9", "lens": None, "movement": "locked", "size": "MCU", "angle": "eye level"},
              "prompt": "# Subject\nAn unarmed protagonist braces.", "lockedLines": [], "mutedLines": [], "beatSheet": None, "targetModel": "minimax-h3",
              "maxChars": None, "variants": [], "history": [], "takes": [], "createdAt": "2026-07-01T00:00:00.000Z", "updatedAt": "2026-07-01T00:00:00.000Z"}]}],
         "characters": [{"id": "char-1", "name": "Protagonist", "notes": "Wiry."}], "artDept": [], "locations": [], "lookbook": [], "references": [],
         "mySetups": [], "createdAt": "2026-07-01T00:00:00.000Z", "updatedAt": "2026-07-01T00:00:00.000Z"}


def test_a_scriptbreak_project_and_its_bundle():
    found = send("AVA.scriptbreak", json.dumps(SCRIPTBREAK).encode()).json()
    assert found["app"] == "scriptbreak" and found["title"] == "AVA"
    assert [one["title"] for one in found["shots"]] == ["SHOT 1A", "SHOT 1B"]
    assert found["shots"][1]["camera"] == "MCU, Eye level, Push in, 50mm lens" and found["shots"][1]["scene"].startswith("EXT. STREET")
    assert {one["name"] for one in found["cast"]} == {"Bartender", "Ava"} and found["locations"][0]["name"] == "Street"
    bundled = send("ava_scriptbreak_bundle.zip", zipped({"ava/README.txt": "x", "ava/ava.scriptbreak": json.dumps(SCRIPTBREAK)})).json()
    assert bundled["app"] == "scriptbreak" and len(bundled["shots"]) == 2


def test_a_scriptbreak_prompt_pack():
    found = send("ava_video_prompts_generic.md", PACK_MD.encode()).json()
    assert found["app"] == "scriptbreak" and found["title"] == "AVA"
    assert [(one["title"], one["camera"]) for one in found["shots"]] == [("SHOT 1A", "EWS, Static"), ("SHOT 1B", "MCU, Eye level, Push in, 50mm lens")]
    assert found["shots"][1]["action"] == "Ava at the mic. Ava broadcasts into the night." and found["shots"][0]["scene"] == "EXT. STREET - NIGHT"
    assert found["cast"] == [{"name": "Ava", "description": "A voice on late-night radio.", "image": None}]
    assert send("notes.md", b"# Just notes\n").status_code == 400


def test_a_cork_board_project():
    found = send("ava.json", json.dumps(CORK).encode()).json()
    assert found["app"] == "cork-board" and [one["title"] for one in found["shots"]] == ["Static"]  # cut and orphan cards are left out
    assert found["shots"][0]["scene"] == "INT. KVOX RADIO STATION - NIGHT" and "With Ava" in found["shots"][0]["notes"]
    assert found["cast"][0]["name"] == "Ava" and found["locations"][0]["name"] == "KVOX Radio Station"


def test_a_master_canvas_project_and_handoff():
    found = send("master-canvas-project.json", json.dumps(MASTER).encode()).json()
    assert found["app"] == "master-canvas" and [one["title"] for one in found["shots"]] == ["Close", "Scene 1 hero shot"]
    assert found["shots"][0]["scene"] == "Opening" and found["shots"][1]["scene"] == "Scene 1" and found["shots"][1]["seconds"] == 6
    assert found["cast"][0]["name"] == "Runner"
    package = zipped({"README.md": "x", "master-canvas-project.json": MASTER, "project_manifest.json": HANDOFF_MANIFEST,
                      "assets/scene-01/Scene1-01-hero.png": PNG, "assets/scene-01/Scene1-02-look.png": PNG})
    found = send("neon-run-handoff-package.zip", package).json()
    assert found["app"] == "master-canvas" and [one["title"] for one in found["shots"]] == ["Hero", "Look back"]
    assert all((HOME / one["image"]).is_file() for one in found["shots"]) and found["shots"][0]["seconds"] == 6
    assert found["cast"][0]["name"] == "Runner"


def test_a_storyboard_reference_board():
    found = send("board.zip", zipped({"board-2026/prompts.json": BOARD, "board-2026/01_shot-1a-hero-enters/still.png": PNG,
                                      "board-2026/01_shot-1a-hero-enters/prompt.txt": "wide shot", "board-2026/02_frame/still.png": PNG,
                                      "board-2026/board.md": "# My Board — storyboard"})).json()
    assert found["app"] == "storyboard-reference" and found["title"] == "My Board"
    assert [one["action"] for one in found["shots"]] == ["wide shot --ar 2.39:1", "a face in rain"]
    assert found["shots"][0]["image"].endswith("01_shot-1a-hero-enters/still.png") and (HOME / found["shots"][1]["image"]).is_file()
    film = client.post(f"/api/suite/imports/{found['id']}/slate", json={}).json()
    shot = client.get(f"/api/slate/projects/{film['project']}").json()["scenes"][0]["shots"][0]
    assert shot["tf"]["start"] == "image" and shot["tf"]["image"] == found["shots"][0]["image"]


def test_a_blockout_export_and_its_motion_pack():
    base = "Scene-1/Shot-1A/export-2026-07-06-08-17-49/"
    package = zipped({base + "metadata.json": BLOCKOUT_META, base + "1A_reference.mp4": b"reference", base + "1A_depth.mp4": b"depth",
                      base + "stills/1A_first.png": PNG, base + "stills/1A_mark-1.png": PNG, base + "prompt.txt": "Cinematic shot on a 35mm lens.\n",
                      base + "README.txt": "x"})
    found = send("shot-1a.zip", package).json()
    assert found["app"] == "blockout" and found["motion"] is True
    one = found["shots"][0]
    assert one["seconds"] == 10.5 and one["action"] == "Cinematic shot on a 35mm lens." and one["scene"] == "Scene 1"
    assert one["reference_video"].endswith("1A_reference.mp4") and one["image"].endswith("stills/1A_first.png")
    assert found["cast"] == [{"name": "Thief", "description": "person.man", "image": None}]
    made = client.post(f"/api/suite/imports/{found['id']}/motion", json={})
    assert made.status_code == 200, made.text
    pack = client.get(f"/api/motion/packs/{made.json()['pack']}").json()
    assert {"reference", "depth"} <= set(pack["layers"]) and pack["mode"] == "actor-motion" and pack["app"] == "Blockout"
    fountain = send("reel.fountain", FOUNTAIN.encode()).json()
    assert client.post(f"/api/suite/imports/{fountain['id']}/motion", json={}).status_code == 400


def test_a_slate_project_goes_to_slate_as_it_is():
    found = send("project.json", json.dumps(SLATE).encode()).json()
    assert found["app"] == "slate" and found["shots"][0]["title"] == "Impact Frame" and found["shots"][0]["seconds"] == 8
    made = client.post(f"/api/suite/imports/{found['id']}/slate", json={}).json()
    film = client.get(f"/api/slate/projects/{made['project']}").json()
    assert film["scenes"][0]["shots"][0]["prompt"] == "# Subject\nAn unarmed protagonist braces." and film["logline"] == "One frame."


def test_a_file_that_is_none_of_them_is_refused():
    assert send("x.json", b'{"hello": 1}').status_code == 400
    assert send("x.zip", zipped({"notes.txt": "hello"})).status_code == 400
    assert send("x.exe", b"MZ").status_code == 400
    assert send("x.zip", b"not a zip").status_code == 400


def test_dailies_for_circle_take():
    clip = HOME / "renders" / "d1.mp4"
    clip.write_bytes(b"clip")
    jobs = [{"id": "1017-120000-aaaaaa", "kind": "video", "status": "done", "title": "12A hero enters", "outputs": ["renders/d1.mp4"],
             "params": {"prompt": "A hero enters.", "seed": 42, "quality": "fh8", "preset": "sharp", "loras": [{"id": "x", "strength": 1}]},
             "engine": "zig", "passes": 8, "created": time.time()},
            {"id": "1017-120001-bbbbbb", "kind": "video", "status": "done", "title": "video", "outputs": ["renders/d1.mp4"],
             "params": {"prompt": "Untitled.", "seed": 7, "preset": "small"}, "created": time.time()},
            {"id": "1017-120002-cccccc", "kind": "video", "status": "running", "title": "12A", "outputs": [], "params": {}}]
    with server.lock:
        for job in jobs:
            server.jobs[job["id"]] = job
    try:
        answer = client.get("/api/export/dailies.zip?jobs=1017-120000-aaaaaa,1017-120001-bbbbbb")
        assert answer.status_code == 200 and answer.headers["content-type"] == "application/zip"
        bundle = zipfile.ZipFile(io.BytesIO(answer.content))
        names = set(bundle.namelist())
        assert {"dailies/12A_take01.mp4", "dailies/12A_take01.json", "dailies/take_1017-120001-bbbbbb.mp4"} <= names
        side = json.loads(bundle.read("dailies/12A_take01.json"))
        assert (side["prompt"], side["seed"], side["model"], side["generator"]) == ("A hero enters.", 42, "fasth3", "Backlot")
        assert side["shot"] == "12A" and side["job"] == "1017-120000-aaaaaa" and side["resolution"] == "1376x768" and side["passes"] == 8
        assert client.get("/api/export/dailies.zip?jobs=1017-120002-cccccc").status_code == 400
        assert client.get("/api/export/dailies.zip?jobs=nope").status_code == 404
    finally:
        with server.lock:
            for job in jobs:
                server.jobs.pop(job["id"], None)
    assert suite.SHOT_CODE.match("take_1017-120001-bbbbbb") is None  # Circle Take leaves it unassigned


def test_the_call_sheet_entry_has_its_apps_json_shape():
    entry = client.get("/api/export/callsheet-entry").json()
    assert set(entry) == {"id", "name", "bundle", "repo", "stage", "tagline"} and entry["repo"] is None
