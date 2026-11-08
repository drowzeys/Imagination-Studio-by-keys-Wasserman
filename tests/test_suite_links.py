"""Previs and prompts brought into a film and the composer: python3 -m pytest tests/test_suite_links.py. The Blockout
export is built here: a one second mp4 from ffmpeg and a one pixel png."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
os.environ.setdefault("STUDIO_GPU_LOCK", str(Path(tempfile.mkdtemp()) / "studio-gpu.lock.d"))
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402
import slate  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
HOME = server.HOME
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\r"
       b"IDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")
META = {"generator": {"app": "Blockout", "schema": 1}, "shot": {"name": "2B", "duration": 1, "fps": 24, "aspect": "16:9", "rig": "dolly"},
        "cameraMarks": [{"index": 1, "time": 0, "focalLength": 50}], "subjects": [], "warnings": []}
need_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is needed to make the sample clip")


def clip() -> bytes:
    with tempfile.TemporaryDirectory() as scratch:
        out = Path(scratch) / "a.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=64x64:d=1:r=12", "-pix_fmt", "yuv420p", str(out)], check=True)
        return out.read_bytes()


def blockout_zip() -> bytes:
    base = "Scene-2/Shot-2B/export-1/"
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as bundle:
        bundle.writestr(base + "metadata.json", json.dumps(META))
        bundle.writestr(base + "prompt.txt", "A courier runs down a wet stair.\n")
        bundle.writestr(base + "2B_reference.mp4", clip())
        bundle.writestr(base + "2B_depth.mp4", clip())
        bundle.writestr(base + "stills/2B_first.png", PNG)
    return data.getvalue()


def bring() -> dict:
    return client.post("/api/suite/import", files={"file": ("shot-2b.zip", blockout_zip())}).json()


def film(prompt: str = "") -> dict:
    project = slate.blank("Links")
    project["scenes"] = [{"id": "sc-1", "name": "Stair", "synopsis": "", "shots": [
        {"id": "sh-1", "name": "Run", "intent": "", "spec": {"durationSec": 4}, "prompt": prompt, "tf": {"start": "text", "lines": []}}]}]
    slate.write(project)
    return project


@need_ffmpeg
def test_a_previs_shot_attaches_to_an_existing_shot():
    found = bring()
    assert found["app"] == "blockout"
    project = film()
    sent = client.post(f"/api/suite/imports/{found['id']}/attach", json={"film": project["id"], "mapping": [{"shot": "s001", "to": "sh-1"}]})
    assert sent.status_code == 200, sent.text
    shot = sent.json()["scenes"][0]["shots"][0]
    tf = shot["tf"]
    assert tf["start"] == "image" and tf["image"].startswith(f"slate/{project['id']}/suite/") and (HOME / tf["image"]).is_file()
    assert tf["reference_video"].endswith("reference.mp4") and (HOME / tf["reference_video"]).is_file()
    assert "A courier runs down a wet stair." in shot["prompt"] and "# Camera" in shot["prompt"]
    assert client.get(f"/api/slate/projects/{project['id']}").json()["scenes"][0]["shots"][0]["tf"]["image"] == tf["image"]


@need_ffmpeg
def test_attach_keeps_a_prompt_that_is_there_and_makes_new_shots():
    found = bring()
    project = film("# Subject\nMy own words.")
    sent = client.post(f"/api/suite/imports/{found['id']}/attach", json={"film": project["id"], "mapping": [
        {"shot": "s001", "to": "sh-1"}, {"shot": "s001", "to": "new", "scene": "sc-1"}]}).json()
    shots = sent["scenes"][0]["shots"]
    assert shots[0]["prompt"] == "# Subject\nMy own words." and len(shots) == 2
    assert shots[1]["name"] == "Shot 2B" and "wet stair" in shots[1]["prompt"] and shots[1]["tf"]["reference_video"]


@need_ffmpeg
def test_attach_refuses_what_it_cannot_place():
    found, project = bring(), film()
    url = f"/api/suite/imports/{found['id']}/attach"
    assert client.post(url, json={"film": project["id"], "mapping": []}).status_code == 400
    assert client.post(url, json={"film": project["id"], "mapping": [{"shot": "nope", "to": "sh-1"}]}).status_code == 400
    assert client.post(url, json={"film": project["id"], "mapping": [{"shot": "s001", "to": "nope"}]}).status_code == 400
    assert client.post(url, json={"film": "p-missing", "mapping": [{"shot": "s001", "to": "sh-1"}]}).status_code == 404
    assert client.post(url, content=b"{}").status_code in (415, 422)  # FastAPI refuses a body that is not JSON before the route runs


@need_ffmpeg
def test_the_prompts_route_gives_one_prompt_per_shot():
    found = bring()
    out = client.get(f"/api/suite/imports/{found['id']}/prompts").json()
    assert len(out) == 1 and out[0]["title"] == "Shot 2B" and out[0]["seconds"] == 1
    assert out[0]["prompt"].startswith("A courier runs down a wet stair.") and "Camera:" in out[0]["prompt"]
    assert (HOME / out[0]["image"]).is_file()
    assert client.get("/api/suite/imports/nope-0/prompts").status_code == 404


def test_the_prompts_route_reads_a_script_with_its_lines():
    text = "INT. HALL - DAY\n\nMARA waits.\n\nMARA\nNow.\n"
    found = client.post("/api/suite/import", files={"file": ("a.fountain", text.encode())}).json()
    out = client.get(f"/api/suite/imports/{found['id']}/prompts").json()
    assert out and "MARA waits." in out[0]["prompt"] and "Mara: “Now.”" in out[0]["prompt"].replace("MARA", "Mara")


@need_ffmpeg
def test_compile_sends_a_previs_clip_as_a_reference_video(monkeypatch):
    found = bring()
    project = film()
    shot = client.post(f"/api/suite/imports/{found['id']}/attach", json={"film": project["id"], "mapping": [{"shot": "s001", "to": "sh-1"}]}).json()["scenes"][0]["shots"][0]
    monkeypatch.setattr(slate, "ready", lambda: {"ref2va": True})
    made = slate.compile_shot(client.get(f"/api/slate/projects/{project['id']}").json(), "sh-1")
    assert made["plan"]["reference_videos"] == [shot["tf"]["reference_video"]] and made["mode"] == "references"
    assert "image" not in made["plan"] and any("first frame" in w for w in made["warnings"])
    monkeypatch.setattr(slate, "ready", lambda: {})
    plain = slate.compile_shot(client.get(f"/api/slate/projects/{project['id']}").json(), "sh-1")
    assert "reference_videos" not in plain["plan"] and plain["plan"]["image"] and any("Ref2VA" in w for w in plain["warnings"])
    # a clip named outside the studio folder, or in a hidden folder of it, is never sent
    monkeypatch.setattr(slate, "ready", lambda: {"ref2va": True})
    outside = Path(tempfile.mkdtemp()) / "elsewhere.mp4"
    outside.write_bytes(b"mp4")
    hidden = HOME / ".studio-agents" / "clip.mp4"
    hidden.parent.mkdir(exist_ok=True)
    hidden.write_bytes(b"mp4")
    for path in ("../" * 12 + str(outside).lstrip("/"), str(outside), ".studio-agents/clip.mp4"):
        named = client.get(f"/api/slate/projects/{project['id']}").json()
        named["scenes"][0]["shots"][0]["tf"]["reference_video"] = path
        assert "reference_videos" not in slate.compile_shot(named, "sh-1")["plan"], path


# ---- the pages (run in node, with the page's helpers stood in for: see test_shell_page.py) ----
sys.path.insert(0, str(ROOT / "tests"))
from test_shell_page import run  # noqa: E402

IMPORTS = """const imp = { id: "i1", app: "blockout", title: "Stair run", shots: [{ id: "s001", title: "Shot 2B", action: "A courier runs." }, { id: "s002", title: "Shot 2C", action: "She turns." }] };
      const film = { id: "p1", name: "Links", scenes: [{ id: "sc-1", name: "Stair", shots: [{ id: "sh-1", name: "Run" }] }] };
      answers["/api/suite/imports/i1"] = imp; answers["/api/slate/projects"] = [{ id: "p1", name: "Links" }]; answers["/api/slate/projects/p1"] = film;
      const later = () => new Promise(r => setTimeout(r, 0));"""


def test_the_import_page_sends_shots_into_a_film():
    out = run(IMPORTS + """
      answers["/api/suite/imports/i1/attach"] = { ...film, name: "Links" };
      const panel = suiteAttachPanel("i1"); await later(); await later();
      out.before = text(panel);
      const pick = find(panel, n => n.tag === "select" && n.attrs["aria-label"] === "Film"); await pick.attrs.onchange({ target: { value: "p1" } }); await later();
      const where = find(panel, n => n.tag === "select" && /Shot 2B/.test(n.attrs["aria-label"] || ""));
      out.options = where.kids.map(o => text(o)); out.defaults = [SLK.panels.i1.map.s001, SLK.panels.i1.map.s002];
      await button(panel, "Attach").attrs.onclick(); await later();
      out.sent = calls.filter(c => c[0] === "/api/suite/imports/i1/attach").slice(-1)[0][1];
      out.after = text(panel);
      SU.apps = []; SU.cur = imp; out.page = text(viewImport());
      SU.imports = [{ id: "i1", app: "blockout", title: "Stair run", shots: 2 }]; const rows = viewImport(); out.rowButtons = text(rows);""")
    assert "Send into a film" in out["before"] and "Choose a film" in out["before"]
    assert out["options"] == ["Do not send", "A new shot", "Stair · Run"] and out["defaults"] == ["sh-1", "new"]
    assert out["sent"] == {"film": "p1", "mapping": [{"shot": "s001", "to": "sh-1"}, {"shot": "s002", "to": "new", "scene": "sc-1"}]}
    assert "Open the film" in out["after"]
    assert "Send into a film" in out["page"] and "Use in the composer" in out["rowButtons"] and "Blockout" in out["rowButtons"]


def test_the_composer_has_a_from_the_suite_chip_that_fills_the_box():
    out = run(IMPORTS + """
      answers["/api/library"] = []; answers["/api/suite/imports"] = [{ id: "i1", app: "blockout", title: "Stair run", shots: 2 }];
      answers["/api/suite/imports/i1/prompts"] = [{ title: "Shot 2B", prompt: "A courier runs.\\n\\nCamera: dolly.", seconds: 4.2, image: "uploads/suite/i1/2B.png" }];
      answers["/api/compose"] = { prompt: "composed" }; answers["/api/jobs"] = { id: "j1", title: "t" }; S.durations = { 5: 121, 8: 193 };
      const sheets = []; document.body.append = n => sheets.push(n);
      const page = viewHome(); out.chip = !!button(page, "From the suite");
      button(page, "From the suite").attrs.onclick(); await later(); await later();
      const sheet = sheets[0]; out.sheet = text(sheet);
      await find(sheet, n => n.tag === "button" && /Stair run/.test(text(n))).attrs.onclick(); await later();
      find(sheet, n => n.attrs && n.attrs["data-pick"] === "Shot 2B").attrs.onclick();
      out.box = [HC.text, HC.seconds, HC.image, HC.mode]; out.note = text(homeComposer());
      await hcGenerate(); out.job = calls.filter(c => c[0] === "/api/jobs").slice(-1)[0][1].params;
      out.compose = calls.filter(c => c[0] === "/api/compose").slice(-1)[0][1].first_frame; out.cleared = HC.image;""")
    assert out["chip"] and "Stair run" in out["sheet"] and "From the suite" in out["sheet"]
    assert out["box"] == ["A courier runs.\n\nCamera: dolly.", 5, "uploads/suite/i1/2B.png", "clip"] and "First frame: 2B.png" in out["note"]
    assert out["job"]["image"] == "uploads/suite/i1/2B.png" and out["compose"] is True and out["cleared"] is None


# ---- the agents' tools ----
from test_studio_mcp_slate import studio, tool  # noqa: E402,F401


def test_an_agent_lists_imports_and_sends_a_shot_into_a_film(studio):
    found = client.post("/api/suite/import", files={"file": ("reel.fountain", b"INT. HALL - DAY\n\nMARA waits.\n")}).json()
    project = film()
    project["name"] = "Agent film"
    slate.write(project)
    listed = tool("suite_imports")
    assert any(one["id"] == found["id"] for one in listed)
    detail = tool("suite_imports", **{"import": found["id"]})
    assert detail["shots"][0]["id"] == "s001" and "MARA waits." in detail["shots"][0]["action"]
    done = tool("suite_attach", **{"import": found["id"], "film": "Agent film", "mapping": [{"shot": "s001", "to": "Run"}]})
    assert done["sent"] == 1 and "MARA waits." in client.get(f"/api/slate/projects/{project['id']}").json()["scenes"][0]["shots"][0]["prompt"]
    again = tool("suite_attach", **{"import": found["id"], "film": project["id"], "mapping": [{"shot": "s001", "to": "new", "scene": "Stair"}]})
    assert len(again["shots"]) == 2
