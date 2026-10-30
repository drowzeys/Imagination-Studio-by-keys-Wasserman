"""The Library knows what is in every clip and picture (app/captions.py): the index, the captioner, search and the routes,
with a stand-in for the vision model. python3 -m pytest tests/test_captions.py"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
sys.path.insert(0, str(ROOT / "app"))
import captions  # noqa: E402
import qa  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
STORE = server.caption_router.captioner["store"]
ANSWERS = {
    "dog": {"caption": "A brown dog chases a red kite across a sunny field.", "tags": ["Dog", "kite", "field", "running", "sunny"], "people": [], "place": "a park", "mood": "playful"},
    "car": {"caption": "A silver car drives along a coast road at dusk.", "tags": ["car", "road", "coast", "dusk", "driving"], "people": ["a driver"], "place": "coast road", "mood": "calm"},
}
LOOKS = []
MINE = ("renders/clip_a.mp4", "renders/clip_b.mp4", "uploads/still.png")


def fake_look(picture):
    mark = Path(picture).read_bytes().decode(errors="ignore")
    LOOKS.append(mark)
    mark = mark[:3]
    return json.dumps(ANSWERS["dog" if mark == "dog" else "car"])


def mine():
    return [m for m in LOOKS if m in ("dog clip", "car clip", "dog still")]


def setup(monkeypatch):
    monkeypatch.setattr(captions, "look", fake_look)
    monkeypatch.setattr(qa, "sheet", lambda clip, target: (target.write_bytes(clip.read_bytes()), [1.0])[1])
    LOOKS.clear()
    monkeypatch.setattr(captions, "PER_ROUND", 100)  # files other tests left in the Library must not crowd these out
    for name in MINE:
        (server.HOME / name).unlink(missing_ok=True)
    (server.HOME / "renders").mkdir(parents=True, exist_ok=True)
    (server.HOME / "uploads").mkdir(parents=True, exist_ok=True)
    STORE.write({})
    (server.HOME / "renders/clip_a.mp4").write_bytes(b"dog clip")
    (server.HOME / "renders/clip_b.mp4").write_bytes(b"car clip")
    (server.HOME / "uploads/still.png").write_bytes(b"dog still")


def test_run_writes_the_index_and_search_ranks_the_dog_clip_first(monkeypatch):
    setup(monkeypatch)
    done = client.post("/api/library/captions/run", json={"paths": ["renders/clip_a.mp4", "renders/clip_b.mp4"]}).json()
    assert sorted(done["done"]) == ["renders/clip_a.mp4", "renders/clip_b.mp4"] and not done["busy"]
    index = json.loads((server.HOME / "library/captions.json").read_text())
    entry = index["renders/clip_a.mp4"]
    assert entry["caption"].startswith("A brown dog") and "dog" in entry["tags"] and entry["model"] and entry["place"] == "a park"
    assert entry["modified"] == (server.HOME / "renders/clip_a.mp4").stat().st_mtime
    one = client.get("/api/library/captions", params={"path": "renders/clip_b.mp4"}).json()
    assert one["caption"].startswith("A silver car")
    found = client.get("/api/library/search", params={"q": "dog chasing kite"}).json()["items"]
    assert found[0]["path"] == "renders/clip_a.mp4" and found[0]["caption"] and found[0]["kind"] == "video"
    assert all(i["path"] != "renders/clip_b.mp4" for i in found)
    assert client.get("/api/library/search", params={"q": "cars on a road"}).json()["items"][0]["path"] == "renders/clip_b.mp4"  # plurals
    assert client.get("/api/library/search", params={"q": ""}).json()["items"] == []


def test_the_prompt_a_clip_was_made_from_is_searched_too(monkeypatch):
    setup(monkeypatch)
    monkeypatch.setattr(server.hub, "takes", lambda: {"renders/clip_b.mp4": {"path": "renders/clip_b.mp4", "prompt": "a lighthouse in a storm"}})
    client.post("/api/library/captions/run", json={"paths": ["renders/clip_b.mp4"]})
    assert client.get("/api/library/captions", params={"path": "renders/clip_b.mp4"}).json()["prompt"] == "a lighthouse in a storm"
    assert client.get("/api/library/search", params={"q": "lighthouse"}).json()["items"][0]["path"] == "renders/clip_b.mp4"


def test_stale_entries_are_dropped_and_forgetting_works(monkeypatch):
    setup(monkeypatch)
    client.post("/api/library/captions/run", json={"paths": ["renders/clip_a.mp4", "renders/clip_b.mp4"]})
    (server.HOME / "renders/clip_b.mp4").unlink()
    STORE.prune()
    assert list(json.loads((server.HOME / "library/captions.json").read_text())) == ["renders/clip_a.mp4"]
    assert client.delete("/api/library/captions", params={"path": "renders/clip_a.mp4"}).json() == {"forgotten": 1}
    assert client.get("/api/library/captions", params={"path": "renders/clip_a.mp4"}).status_code == 404


def test_a_round_describes_new_and_changed_files_only_when_idle_and_switched_on(monkeypatch):
    setup(monkeypatch)
    route = server.caption_router.captioner
    monkeypatch.setattr(server.settings, "load", lambda: {"presets": {"captions_auto": False}})
    route["round"]()
    assert not mine()  # switched off
    monkeypatch.setattr(server.settings, "load", lambda: {"presets": {"captions_auto": True}})
    running = server.running["process"]
    server.running["process"] = object()  # a job holds the machine
    try:
        route["round"]()
        assert not mine()
    finally:
        server.running["process"] = running
    route["round"]()
    assert len(mine()) == 3  # two clips and a picture, once each
    route["round"]()
    assert len(mine()) == 3  # nothing changed
    os.utime(server.HOME / "renders/clip_a.mp4", (1, 1))
    route["round"]()
    assert len(mine()) == 4  # the changed file is described again


def test_a_busy_model_stops_the_round_quietly_and_bad_answers_are_skipped(monkeypatch):
    setup(monkeypatch)
    route = server.caption_router.captioner

    def busy(picture):
        raise captions.Busy("a render has the memory")

    monkeypatch.setattr(captions, "look", busy)
    assert route["round"]() is None and STORE.read() == {}
    out = client.post("/api/library/captions/run", json={"paths": ["renders/clip_a.mp4"]}).json()
    assert out["busy"] and out["done"] == []
    monkeypatch.setattr(captions, "look", lambda picture: "no json here")
    out = client.post("/api/library/captions/run", json={"paths": ["renders/clip_a.mp4"]}).json()
    assert out["failed"] == ["renders/clip_a.mp4"] and STORE.read() == {}


def test_run_with_many_paths_starts_the_thread_and_a_bad_path_is_refused(monkeypatch):
    setup(monkeypatch)
    monkeypatch.setattr(captions, "NOW_LIMIT", 1)
    assert client.post("/api/library/captions/run", json={"paths": ["renders/clip_a.mp4", "renders/clip_b.mp4"]}).json() == {"started": 2}
    assert client.post("/api/library/captions/run", json={"paths": ["../etc/passwd"]}).status_code == 400
    assert client.post("/api/library/captions/run", json={"paths": ["renders/missing.mp4"]}).status_code == 400


def test_the_setting_is_on_by_default_and_checked():
    assert server.settings.load()["presets"]["captions_auto"] is True
    assert client.post("/api/settings", json={"presets": {"captions_auto": "yes"}}).status_code == 400


def test_a_png_goes_to_the_vision_model_as_a_png(tmp_path, monkeypatch):
    sent = []

    class Answer:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode()

    monkeypatch.setattr(captions.urllib.request, "urlopen", lambda request, timeout=None: sent.append(json.loads(request.data)) or Answer())
    for name in ("still.png", "frame.jpg"):
        (tmp_path / name).write_bytes(b"picture")
        captions._vision(tmp_path / name, "http://127.0.0.1:9", "a-model")
    urls = [one["messages"][0]["content"][1]["image_url"]["url"] for one in sent]
    assert urls[0].startswith("data:image/png;base64,") and urls[1].startswith("data:image/jpeg;base64,")


def test_a_bolt_without_vision_is_not_loaded_for_a_caption(tmp_path, monkeypatch):
    """The Metal Bolt (no vision tower) is never started for a caption: the round stops quietly instead (Busy), so an
    idle Bolt stays unloaded. With a vision tower the engine is asked as before."""
    import bolt

    asked = []

    class Engine:
        vision = False

        def request(self, stopped=None, wait=True, say=None):
            asked.append(wait)
            raise AssertionError("loaded for a caption")

    blind = Engine()
    monkeypatch.setattr(qa, "default_brain", lambda: {"endpoint": bolt.ENDPOINT, "model": ""})
    monkeypatch.setattr(qa, "bolt_engine", lambda endpoint: bolt)
    monkeypatch.setattr(bolt, "engine", lambda: blind)
    (tmp_path / "still.png").write_bytes(b"picture")
    try:
        captions.ask_model(tmp_path / "still.png")
        raise AssertionError("no Busy")
    except captions.Busy as busy:
        assert "no vision tower" in str(busy)
    assert asked == []

    import contextlib
    blind.vision = True
    blind.request = lambda stopped=None, wait=True, say=None: (asked.append(wait), contextlib.nullcontext("http://127.0.0.1:9/v1"))[1]
    monkeypatch.setattr(captions, "_vision", lambda picture, live, model: f"{live} {model}")
    assert captions.ask_model(tmp_path / "still.png") == "http://127.0.0.1:9/v1 " + bolt.SERVED and asked == [False]
